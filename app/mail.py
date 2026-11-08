# Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
# CustomMail | https://github.com/thanush0/custommail
import imaplib
import smtplib
import ssl
from email.message import EmailMessage
from email import message_from_bytes
from email.header import decode_header, make_header
from .config import settings

def _imap(username: str, password: str):
    if settings.mail_tls:
        client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, ssl_context=ssl.create_default_context())
    else:
        client = imaplib.IMAP4(settings.imap_host, settings.imap_port)
    client.login(username, password)
    return client

def list_inbox(username: str, password: str, limit: int = 50):
    client = _imap(username, password)
    try:
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("Unable to open INBOX")
        typ, data = client.search(None, "ALL")
        ids = data[0].split()[-limit:] if data and data[0] else []
        result = []
        for mid in reversed(ids):
            typ, msgdata = client.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS)")
            raw = next((part[1] for part in msgdata if isinstance(part, tuple)), b"")
            msg = message_from_bytes(raw)
            result.append({
                "id": mid.decode(),
                "from": str(make_header(decode_header(msg.get("From", "")))),
                "subject": str(make_header(decode_header(msg.get("Subject", "(no subject)")))),
                "date": msg.get("Date", ""),
                "seen": b"\\Seen" in str(msgdata).encode()
            })
        return result
    finally:
        try: client.logout()
        except Exception: pass

def read_message(username: str, password: str, message_id: str):
    client = _imap(username, password)
    try:
        client.select("INBOX")
        typ, data = client.fetch(message_id.encode(), "(BODY.PEEK[])")
        raw = next((part[1] for part in data if isinstance(part, tuple)), b"")
        msg = message_from_bytes(raw)
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    payload = part.get_payload(decode=True) or b""
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    break
        else:
            payload = msg.get_payload(decode=True) or b""
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return {"id": message_id, "from": msg.get("From", ""), "to": msg.get("To", ""),
                "subject": msg.get("Subject", ""), "date": msg.get("Date", ""), "body": body[:100000]}
    finally:
        try: client.logout()
        except Exception: pass

def send_message(username: str, password: str, to: str, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = to
    msg["Subject"] = subject[:250]
    msg.set_content(body[:100000])
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.mail_tls:
            server.starttls(context=context)
        server.login(username, password)
        server.send_message(msg)

def delete_message(username: str, password: str, message_id: str):
    client = _imap(username, password)
    try:
        client.select("INBOX")
        client.store(message_id.encode(), "+FLAGS", "\\\\Deleted")
        client.expunge()
    finally:
        try: client.logout()
        except Exception: pass
