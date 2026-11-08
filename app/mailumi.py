import asyncio
import base64
import hashlib
import hmac
import html
import re
import time
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import User, Message, Attachment, AuditLog, WebhookEvent, TempMailbox
from .temp_mail import cleanup_expired_temp_mailboxes


def verify_mailumi_signature(raw_body: bytes, headers, secret: str) -> dict:
    event_id = headers.get("webhook-id", "").strip()
    timestamp = headers.get("webhook-timestamp", "").strip()
    signatures = headers.get("webhook-signature", "").split()

    if not secret.startswith("whsec_") or not event_id or not timestamp.isdigit():
        raise ValueError("Invalid webhook signature")
    if abs(time.time() - int(timestamp)) > 300:
        raise ValueError("Webhook timestamp outside the allowed window")

    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid webhook secret") from None

    signed = f"{event_id}.{timestamp}.".encode("utf-8") + raw_body
    expected = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()
    ).decode("ascii")

    if not any(
        item.startswith("v1,") and hmac.compare_digest(item[3:], expected)
        for item in signatures
    ):
        raise ValueError("Invalid webhook signature")

    try:
        event = __import__("json").loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid webhook JSON") from None

    if not isinstance(event, dict) or event.get("id") != event_id:
        raise ValueError("Webhook event ID mismatch")
    return event


async def _download_attachments(items, api_key: str):
    if not items:
        return []
    if not api_key:
        raise RuntimeError("Mailumi attachment API key is not configured")
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("Invalid attachment list")

    total_expected = 0
    for item in items:
        try:
            size = int(item.get("size", 0))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Invalid attachment size") from None
        if size < 0:
            raise ValueError("Invalid attachment size")
        total_expected += size
    if total_expected > 10 * 1024 * 1024:
        raise ValueError("Mailumi attachment total exceeds 10 MB")

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.5), follow_redirects=False) as client:
        async def fetch(item):
            url = str(item.get("download_url", ""))
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "mailumi.com" or parsed.port not in (None, 443):
                raise ValueError("Untrusted attachment download URL")
            async with semaphore:
                chunks = []
                received = 0
                async with client.stream(
                    "GET", url, headers={"Authorization": f"Bearer {api_key}"}
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > 10 * 1024 * 1024:
                            raise ValueError("Attachment exceeds 10 MB")
                        chunks.append(chunk)
                content = b"".join(chunks)
            expected_size = int(item.get("size", 0))
            if len(content) > 10 * 1024 * 1024 or (expected_size and len(content) != expected_size):
                raise ValueError("Attachment size does not match webhook metadata")
            name = Path(str(item.get("filename") or "attachment").replace("\\", "/")).name
            name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:180] or "attachment"
            content_type = str(item.get("content_type") or "application/octet-stream")[:120]
            return name, content_type, content

        return await asyncio.wait_for(
            asyncio.gather(*(fetch(item) for item in items)),
            timeout=5.0,
        )


async def ingest_mailumi_event(
    db, event: dict, domain: str, storage_dir: str, api_key: str
):
    event_id = str(event.get("id", "")).strip()
    event_type = str(event.get("type", ""))[:120]
    if not event_id or len(event_id) > 255:
        raise ValueError("Invalid webhook event ID")
    if event_type != "email.received":
        return {"ok": True, "ignored": True}

    existing_event = db.scalar(
        select(WebhookEvent.id).where(WebhookEvent.event_id == event_id)
    )
    if existing_event:
        return {"ok": True, "duplicate": True}

    data = event.get("data")
    if not isinstance(data, dict):
        raise ValueError("Invalid incoming email payload")

    email_id = str(data.get("email_id") or "").strip()
    if not email_id or len(email_id) > 255:
        raise ValueError("Missing Mailumi email ID")

    cleanup_expired_temp_mailboxes(db, storage_dir)

    raw_recipients = data.get("to", [])
    raw_cc = data.get("cc", [])
    if not isinstance(raw_recipients, list) or not isinstance(raw_cc, list):
        raise ValueError("Invalid email recipient list")

    recipients = set()
    for address in raw_recipients + raw_cc:
        if isinstance(address, str) and "@" in address:
            address = address.strip().lower()
            if address.rsplit("@", 1)[1] == domain.lower():
                recipients.add(address)
    if not recipients:
        raise ValueError("No recipient for this mail domain")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    active_users = {
        user.email
        for user in db.scalars(
            select(User).where(User.email.in_(recipients), User.active.is_(True))
        ).all()
    }
    active_temp = {
        box.email
        for box in db.scalars(
            select(TempMailbox).where(
                TempMailbox.email.in_(recipients),
                TempMailbox.active.is_(True),
                TempMailbox.expires_at > now,
            )
        ).all()
    }
    active_recipients = active_users | active_temp
    if not active_recipients:
        raise ValueError("No active mailbox found for recipient")

    sender = parseaddr(str(data.get("from") or ""))[1].strip().lower()
    if not sender or "@" not in sender or len(sender) > 255:
        raise ValueError("Invalid sender address")

    subject = str(data.get("subject") or "")[:250]
    message_id = str(data.get("message_id") or f"mailumi:{email_id}")[:255]
    body = str(data.get("text") or "")[:500000]
    if not body and data.get("html"):
        markup = str(data["html"])
        markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
        body = html.unescape(re.sub(r"<[^>]+>", " ", markup))
        body = re.sub(r"[ \t]+", " ", body).strip()[:500000]

    downloaded = await _download_attachments(data.get("attachments", []), api_key)
    storage = Path(storage_dir) / "attachments"
    storage.mkdir(parents=True, exist_ok=True)
    created_files = []
    stored = 0
    duplicates = 0
    try:
        db.add(WebhookEvent(event_id=event_id, event_type=event_type))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"ok": True, "duplicate": True}

        for recipient in sorted(active_recipients):
            user = db.scalar(
                select(User).where(User.email == recipient, User.active.is_(True))
            )
            temp_box = None
            if not user:
                temp_box = db.scalar(
                    select(TempMailbox.id).where(
                        TempMailbox.email == recipient,
                        TempMailbox.active.is_(True),
                        TempMailbox.expires_at > now,
                    )
                )
            if not user and not temp_box:
                continue

            exists = db.scalar(
                select(Message.id).where(
                    Message.owner_email == recipient,
                    Message.message_id == message_id,
                )
            )
            if exists:
                duplicates += 1
                continue

            row = Message(
                owner_email=recipient,
                sender=sender,
                recipients=", ".join(sorted(recipients)),
                subject=subject,
                body_text=body,
                body_html="",
                folder="spam" if data.get("spam") is True else "inbox",
                delivery_status="received",
                message_id=message_id,
            )
            db.add(row)
            db.flush()

            for filename, content_type, content in downloaded:
                storage_name = uuid4().hex
                path = storage / storage_name
                path.write_bytes(content)
                created_files.append(path)
                db.add(
                    Attachment(
                        message_id=row.id,
                        filename=filename,
                        content_type=content_type,
                        storage_name=storage_name,
                        size_bytes=len(content),
                    )
                )

            db.add(
                AuditLog(
                    actor="mailumi-webhook",
                    action="mail.received",
                    detail=f"recipient={recipient}; sender={sender}; email_id={email_id}",
                )
            )
            stored += 1

        db.commit()
        return {"ok": True, "stored": stored, "duplicates": duplicates}
    except Exception:
        db.rollback()
        for path in created_files:
            path.unlink(missing_ok=True)
        raise


def send_mailumi_email(api_key, sender, to, subject, text, cc=None, bcc=None, idempotency_key=None):
    if not api_key:
        raise RuntimeError("Mailumi sending API key is not configured")
    if not to:
        raise ValueError("Mailumi requires at least one To recipient")

    payload = {
        "from": sender,
        "to": list(to),
        "subject": subject[:250],
        "text": text[:100000],
    }
    if cc:
        payload["cc"] = list(cc)
    if bcc:
        payload["bcc"] = list(bcc)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "CustomMail/2.0",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]

    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        response = client.post(
            "https://mailumi.com/api/v1/emails",
            headers=headers,
            json=payload,
        )

    if not response.is_success:
        raise RuntimeError(f"Mailumi API returned HTTP {response.status_code}")

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Mailumi API returned invalid JSON") from None

    if not isinstance(result, dict) or not result.get("id") or result.get("status") != "queued":
        raise RuntimeError("Mailumi API did not confirm that the email was queued")

    return result
