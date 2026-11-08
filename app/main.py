from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta
import re
import secrets
import time
import hashlib
from collections import defaultdict, deque
import hmac
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_

from .config import settings
from .db import Base, engine, get_db
from .models import Mailbox, User, Message, Attachment, AuditLog, TempMailbox
from .security import hash_password, verify_password, create_token, decode_token, check_login_rate
from .inbound import ingest_email
from .mailumi import verify_mailumi_signature, ingest_mailumi_event, send_mailumi_email
from .temp_mail import hash_temp_token, purge_temp_mailbox, cleanup_expired_temp_mailboxes

DOMAIN = settings.mail_domain.lower()
STORAGE = Path(settings.storage_dir)
ATTACHMENTS = STORAGE / "attachments"
MAX_UPLOAD = settings.max_attachment_mb * 1024 * 1024
TEMP_CREATE_ATTEMPTS = defaultdict(deque)
TEMP_MAIL_LIFETIME = timedelta(hours=24)
TEMP_MAIL_MAX_ACTIVE_PER_IP = 5
TEMP_MAIL_CREATE_LIMIT_PER_HOUR = 10

@asynccontextmanager
async def lifespan(app: FastAPI):
    STORAGE.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    if settings.bootstrap_admin_enabled:
        from .db import SessionLocal
        db = SessionLocal()
        try:
            admin_email = f"{settings.admin_username.lower()}@{DOMAIN}"
            existing = db.scalar(select(User).where(User.email == admin_email))
            if not existing:
                db.add(User(email=admin_email, display_name="Administrator",
                            password_hash=hash_password(settings.admin_password), is_admin=True))
                db.commit()
        finally:
            db.close()
    yield

app = FastAPI(title="CustomMail", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=12, max_length=128)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class AdminLoginIn(BaseModel):
    username: str
    password: str

class ComposeIn(BaseModel):
    to: list[EmailStr] = Field(min_length=1, max_length=20)
    cc: list[EmailStr] = Field(default_factory=list, max_length=20)
    bcc: list[EmailStr] = Field(default_factory=list, max_length=20)
    subject: str = Field(default="", max_length=250)
    body: str = Field(default="", max_length=100000)
    folder: str = "sent"

def _cookie(response: Response, token: str, name: str = "cm_session"):
    response.set_cookie(name, token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.session_max_age_seconds,
                        path="/")

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("cm_session")
    if not token:
        raise HTTPException(401, "Sign in required")
    payload = decode_token(token)
    user = db.scalar(select(User).where(User.email == payload["sub"], User.active.is_(True)))
    if not user:
        raise HTTPException(401, "Account unavailable")
    return user

def admin_required(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return user


def _temp_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _temp_ip_hash(ip: str) -> str:
    return hashlib.sha256(
        f"{settings.app_secret}:custommail-temp:{ip}".encode("utf-8")
    ).hexdigest()


def _check_temp_creation_limit(request: Request, db: Session) -> str:
    ip = _temp_client_ip(request)
    now_mono = time.monotonic()
    attempts = TEMP_CREATE_ATTEMPTS[ip]
    while attempts and attempts[0] <= now_mono - 3600:
        attempts.popleft()
    if len(attempts) >= TEMP_MAIL_CREATE_LIMIT_PER_HOUR:
        raise HTTPException(429, "Too many temporary inboxes created. Try again later.")
    ip_hash = _temp_ip_hash(ip)
    now = datetime.now(timezone.utc)
    active_count = db.scalar(
        select(func.count(TempMailbox.id)).where(
            TempMailbox.create_ip_hash == ip_hash,
            TempMailbox.active.is_(True),
            TempMailbox.expires_at > now,
        )
    ) or 0
    if active_count >= TEMP_MAIL_MAX_ACTIVE_PER_IP:
        raise HTTPException(429, "You already have the maximum number of active temporary inboxes.")
    attempts.append(now_mono)
    return ip_hash


def current_temp_mailbox(
    request: Request, db: Session = Depends(get_db)
) -> TempMailbox:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 128:
        raise HTTPException(401, "Temporary inbox token required")
    token_hash = hash_temp_token(token)
    mailbox = db.scalar(
        select(TempMailbox).where(
            TempMailbox.token_hash == token_hash,
            TempMailbox.active.is_(True),
            TempMailbox.expires_at > datetime.now(timezone.utc),
        )
    )
    if mailbox:
        return mailbox
    expired_or_deleted = db.scalar(
        select(TempMailbox).where(TempMailbox.token_hash == token_hash)
    )
    if expired_or_deleted:
        if expired_or_deleted.active:
            purge_temp_mailbox(db, expired_or_deleted, settings.storage_dir)
            db.commit()
        raise HTTPException(410, "This temporary inbox has expired or was deleted.")
    raise HTTPException(401, "Invalid temporary inbox token")

def audit(db: Session, actor: str, action: str, detail: str = ""):
    db.add(AuditLog(actor=actor, action=action, detail=detail[:1000]))

def _message_json(row: Message, db: Session):
    files = db.scalars(select(Attachment).where(Attachment.message_id == row.id)).all()
    return {
        "id": row.id, "sender": row.sender, "recipients": row.recipients,
        "subject": row.subject, "body": row.body_text, "folder": row.folder,
        "is_read": row.is_read, "is_starred": row.is_starred,
        "delivery_status": row.delivery_status,
        "created_at": row.created_at.isoformat(),
        "attachments": [{"id": a.id, "filename": a.filename, "size": a.size_bytes} for a in files]
    }

@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/temp-mail")
def temp_mail_page():
    return FileResponse("static/temp-mail.html")

@app.post("/api/inbound/email")
async def inbound_email(request: Request, db: Session = Depends(get_db)):
    secret=settings.inbound_email_secret
    supplied=request.headers.get("X-Inbound-Secret","")
    if not secret: raise HTTPException(503,"Inbound email gateway is not configured")
    if not hmac.compare_digest(secret,supplied): raise HTTPException(401,"Invalid gateway credentials")
    envelope_to=request.headers.get("X-Envelope-To","")
    if not envelope_to: raise HTTPException(400,"Missing envelope recipient")
    limit=25*1024*1024
    length=request.headers.get("content-length","")
    if length.isdigit() and int(length)>limit: raise HTTPException(413,"Email exceeds maximum size")
    chunks=[]; size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>limit: raise HTTPException(413,"Email exceeds maximum size")
        chunks.append(chunk)
    try:
        return ingest_email(db,b"".join(chunks),envelope_to,DOMAIN,settings.storage_dir,MAX_UPLOAD)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@app.post("/api/webhooks/mailumi")
async def mailumi_webhook(request: Request, db: Session = Depends(get_db)):
    secret = settings.mailumi_webhook_secret
    if not secret:
        raise HTTPException(503, "Mailumi webhook is not configured")
    limit = 30 * 1024 * 1024
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise HTTPException(413, "Webhook payload exceeds maximum size")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "Webhook payload exceeds maximum size")
        chunks.append(chunk)
    raw_body = b"".join(chunks)
    try:
        event = verify_mailumi_signature(raw_body, request.headers, secret)
    except ValueError as exc:
        raise HTTPException(401, "Invalid Mailumi webhook signature") from exc
    try:
        return await ingest_mailumi_event(
            db, event, DOMAIN, settings.storage_dir, settings.mailumi_api_key
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/health")
def health():
    return {"ok": True, "service": "custommail", "version": app.version,
            "email_domain": DOMAIN, "external_smtp_configured": bool(settings.smtp_host)}

@app.get("/api/config")
def public_config():
    return {"domain": DOMAIN, "registration_enabled": settings.registration_enabled}


@app.post("/api/temp-mailboxes")
def create_temp_mailbox(request: Request, db: Session = Depends(get_db)):
    cleanup_expired_temp_mailboxes(db, settings.storage_dir)
    ip_hash = _check_temp_creation_limit(request, db)
    email = None
    for _ in range(10):
        candidate = f"tmp-{secrets.token_hex(8)}@{DOMAIN}"
        conflict = (
            db.scalar(select(TempMailbox.id).where(TempMailbox.email == candidate))
            or db.scalar(select(User.id).where(User.email == candidate))
            or db.scalar(select(Mailbox.id).where(Mailbox.email == candidate))
        )
        if not conflict:
            email = candidate
            break
    if not email:
        raise HTTPException(503, "Could not allocate a temporary address. Try again.")
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    mailbox = TempMailbox(
        email=email,
        token_hash=hash_temp_token(raw_token),
        create_ip_hash=ip_hash,
        active=True,
        created_at=now,
        expires_at=now + TEMP_MAIL_LIFETIME,
    )
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)
    return {
        "email": mailbox.email,
        "token": raw_token,
        "created_at": mailbox.created_at.isoformat(),
        "expires_at": mailbox.expires_at.isoformat(),
        "lifetime_seconds": int(TEMP_MAIL_LIFETIME.total_seconds()),
    }


@app.get("/api/temp-mailboxes/current")
def get_temp_mailbox(mailbox: TempMailbox = Depends(current_temp_mailbox)):
    return {
        "email": mailbox.email,
        "created_at": mailbox.created_at.isoformat(),
        "expires_at": mailbox.expires_at.isoformat(),
    }


@app.get("/api/temp-mailboxes/current/messages")
def list_temp_messages(
    folder: str = "all",
    limit: int = 100,
    mailbox: TempMailbox = Depends(current_temp_mailbox),
    db: Session = Depends(get_db),
):
    if folder not in {"all", "inbox", "spam"}:
        raise HTTPException(400, "Invalid temporary inbox folder")
    stmt = select(Message).where(Message.owner_email == mailbox.email)
    if folder != "all":
        stmt = stmt.where(Message.folder == folder)
    else:
        stmt = stmt.where(Message.folder.in_(["inbox", "spam"]))
    rows = db.scalars(
        stmt.order_by(Message.created_at.desc()).limit(min(max(limit, 1), 100))
    ).all()
    return [_message_json(row, db) for row in rows]


@app.get("/api/temp-mailboxes/current/messages/{message_id}")
def get_temp_message(
    message_id: int,
    mailbox: TempMailbox = Depends(current_temp_mailbox),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(Message).where(
            Message.id == message_id, Message.owner_email == mailbox.email
        )
    )
    if not row:
        raise HTTPException(404, "Message not found")
    row.is_read = True
    db.commit()
    return _message_json(row, db)


@app.delete("/api/temp-mailboxes/current/messages/{message_id}")
def delete_temp_message(
    message_id: int,
    mailbox: TempMailbox = Depends(current_temp_mailbox),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(Message).where(
            Message.id == message_id, Message.owner_email == mailbox.email
        )
    )
    if not row:
        raise HTTPException(404, "Message not found")
    files = db.scalars(
        select(Attachment).where(Attachment.message_id == row.id)
    ).all()
    for item in files:
        (ATTACHMENTS / item.storage_name).unlink(missing_ok=True)
        db.delete(item)
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/api/temp-mailboxes/current/attachments/{attachment_id}")
def download_temp_attachment(
    attachment_id: int,
    mailbox: TempMailbox = Depends(current_temp_mailbox),
    db: Session = Depends(get_db),
):
    item = db.get(Attachment, attachment_id)
    if not item:
        raise HTTPException(404, "Attachment not found")
    row = db.scalar(
        select(Message).where(
            Message.id == item.message_id, Message.owner_email == mailbox.email
        )
    )
    if not row:
        raise HTTPException(404, "Attachment not found")
    path = ATTACHMENTS / item.storage_name
    if not path.is_file():
        raise HTTPException(404, "Attachment data missing")
    return FileResponse(path, filename=item.filename, media_type=item.content_type)


@app.delete("/api/temp-mailboxes/current")
def delete_temp_mailbox(
    mailbox: TempMailbox = Depends(current_temp_mailbox),
    db: Session = Depends(get_db),
):
    deleted_messages = purge_temp_mailbox(db, mailbox, settings.storage_dir)
    db.commit()
    return {"ok": True, "deleted_messages": deleted_messages}

@app.post("/api/register")
def register(payload: RegisterIn, response: Response, request: Request, db: Session = Depends(get_db)):
    if not settings.registration_enabled:
        raise HTTPException(403, "Registration is disabled")
    username = payload.username.lower()
    email = f"{username}@{DOMAIN}"
    if db.scalar(select(User).where(User.email == email)) or db.scalar(select(Mailbox).where(Mailbox.email == email)):
        raise HTTPException(409, "That email address is already registered")
    user = User(email=email, display_name=payload.display_name.strip(), password_hash=hash_password(payload.password))
    mailbox = Mailbox(username=username, email=email, quota_mb=settings.mailbox_quota_mb, active=True)
    db.add_all([user, mailbox])
    audit(db, email, "account.registered")
    db.commit()
    _cookie(response, create_token(email))
    return {"ok": True, "email": email}

@app.post("/api/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    check_login_rate(request)
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email, User.active.is_(True)))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email address or password")
    _cookie(response, create_token(email))
    audit(db, email, "account.login")
    db.commit()
    return {"ok": True, "email": email, "display_name": user.display_name, "is_admin": user.is_admin}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("cm_session", path="/")
    return {"ok": True}

@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"email": user.email, "display_name": user.display_name, "is_admin": user.is_admin}

@app.get("/api/mail/folders")
def folders():
    return ["inbox", "starred", "sent", "drafts", "spam", "trash"]

@app.get("/api/mail/messages")
def list_messages(folder: str = "inbox", q: str = "", limit: int = 100,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    if folder not in {"inbox", "starred", "sent", "drafts", "spam", "trash"}:
        raise HTTPException(400, "Invalid folder")
    stmt = select(Message).where(Message.owner_email == user.email)
    if folder == "starred":
        stmt = stmt.where(Message.is_starred.is_(True), Message.folder.notin_(["trash", "spam"]))
    else:
        stmt = stmt.where(Message.folder == folder)
    if q.strip():
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Message.subject.ilike(like), Message.sender.ilike(like), Message.body_text.ilike(like)))
    rows = db.scalars(stmt.order_by(Message.created_at.desc()).limit(min(max(limit, 1), 200))).all()
    return [_message_json(r, db) for r in rows]

@app.get("/api/mail/messages/{message_id}")
def get_message(message_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.owner_email == user.email))
    if not row:
        raise HTTPException(404, "Message not found")
    row.is_read = True
    db.commit()
    return _message_json(row, db)

@app.post("/api/mail/messages")
def compose(payload: ComposeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    to_list = list(dict.fromkeys(str(x).lower() for x in payload.to))
    cc_list = list(dict.fromkeys(str(x).lower() for x in payload.cc))
    bcc_list = list(dict.fromkeys(str(x).lower() for x in payload.bcc))
    all_recipients = list(dict.fromkeys(to_list + cc_list + bcc_list))

    if not all_recipients:
        raise HTTPException(400, "Add at least one recipient")

    if payload.folder == "drafts":
        draft = Message(
            owner_email=user.email, sender=user.email,
            recipients=", ".join(all_recipients),
            subject=payload.subject, body_text=payload.body,
            folder="drafts", delivery_status="draft",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return {"ok": True, "id": draft.id, "status": "draft"}

    def is_internal(address):
        return address.endswith("@" + DOMAIN)

    external_to = [x for x in to_list if not is_internal(x)]
    external_cc = [x for x in cc_list if not is_internal(x) and x not in to_list]
    external_bcc = [x for x in bcc_list if not is_internal(x) and x not in to_list and x not in cc_list]
    external_count = len(external_to) + len(external_cc) + len(external_bcc)

    if external_count and not external_to:
        raise HTTPException(
            400,
            "Include at least one external address in To when sending to external CC or BCC recipients.",
        )
    if external_count > 50:
        raise HTTPException(400, "Mailumi allows at most 50 recipients per email.")

    internal = [x for x in all_recipients if is_internal(x)]
    sent = Message(
        owner_email=user.email,
        sender=user.email,
        recipients=", ".join(all_recipients),
        subject=payload.subject,
        body_text=payload.body,
        folder="sent",
        delivery_status="sent_internal" if not external_count else "queued_external",
    )
    db.add(sent)
    db.flush()

    for recipient in internal:
        target = db.scalar(
            select(User).where(User.email == recipient, User.active.is_(True))
        )
        if target:
            db.add(Message(
                owner_email=recipient,
                sender=user.email,
                recipients=recipient,
                subject=payload.subject,
                body_text=payload.body,
                folder="inbox",
                delivery_status="received",
            ))

    audit(db, user.email, "mail.send", f"recipients={len(all_recipients)}")
    db.commit()
    db.refresh(sent)

    provider_id = None
    if external_count:
        if not settings.mailumi_send_api_key:
            sent.delivery_status = "external_blocked_no_mailumi_api"
        else:
            try:
                result = send_mailumi_email(
                    settings.mailumi_send_api_key,
                    user.email,
                    external_to,
                    payload.subject,
                    payload.body,
                    cc=external_cc,
                    bcc=external_bcc,
                    idempotency_key=f"custommail-message-{sent.id}",
                )
                sent.delivery_status = "submitted_to_mailumi"
                provider_id = str(result["id"])[:255]
                audit(
                    db, user.email, "mail.mailumi_submitted",
                    f"message_id={sent.id}; provider_id={provider_id}",
                )
            except Exception as exc:
                sent.delivery_status = "delivery_failed"
                audit(
                    db, user.email, "mail.mailumi_failed",
                    f"message_id={sent.id}; error_type={type(exc).__name__}",
                )
        db.commit()
        db.refresh(sent)

    internal_delivered = sum(
        1 for address in internal
        if db.scalar(select(User.id).where(User.email == address, User.active.is_(True))) is not None
    )
    return {
        "ok": True,
        "id": sent.id,
        "status": sent.delivery_status,
        "internal_delivered": internal_delivered,
        "external_count": external_count,
        "provider_id": provider_id,
    }


@app.patch("/api/mail/messages/{message_id}/star")
def star_message(message_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.owner_email == user.email))
    if not row: raise HTTPException(404, "Message not found")
    row.is_starred = not row.is_starred
    db.commit()
    return {"is_starred": row.is_starred}

@app.delete("/api/mail/messages/{message_id}")
def move_to_trash(message_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.owner_email == user.email))
    if not row: raise HTTPException(404, "Message not found")
    row.folder = "trash"
    db.commit()
    return {"ok": True}

@app.post("/api/mail/messages/{message_id}/restore")
def restore_message(message_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.owner_email == user.email))
    if not row: raise HTTPException(404, "Message not found")
    row.folder = "inbox"
    db.commit()
    return {"ok": True}

@app.post("/api/mail/attachments/{message_id}")
async def upload_attachment(message_id: int, file: UploadFile = File(...),
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.owner_email == user.email))
    if not row: raise HTTPException(404, "Message not found")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD: raise HTTPException(413, "Attachment exceeds configured size limit")
    if not data: raise HTTPException(400, "Empty attachment")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "attachment").name)[:180] or "attachment"
    storage_name = uuid4().hex
    (ATTACHMENTS / storage_name).write_bytes(data)
    item = Attachment(message_id=row.id, filename=safe_name,
                       content_type=(file.content_type or "application/octet-stream")[:120],
                       storage_name=storage_name, size_bytes=len(data))
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "filename": item.filename, "size": item.size_bytes}

@app.get("/api/mail/attachments/{attachment_id}")
def download_attachment(attachment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Attachment, attachment_id)
    if not item: raise HTTPException(404, "Attachment not found")
    msg = db.scalar(select(Message).where(Message.id == item.message_id, Message.owner_email == user.email))
    if not msg: raise HTTPException(404, "Attachment not found")
    path = ATTACHMENTS / item.storage_name
    if not path.is_file(): raise HTTPException(404, "Attachment data missing")
    return FileResponse(path, filename=item.filename, media_type=item.content_type)

@app.get("/api/admin/overview")
def admin_overview(user: User = Depends(admin_required), db: Session = Depends(get_db)):
    return {"users": db.scalar(select(func.count(User.id))) or 0,
            "mailboxes": db.scalar(select(func.count(Mailbox.id))) or 0,
            "messages": db.scalar(select(func.count(Message.id))) or 0,
            "active_users": db.scalar(select(func.count(User.id)).where(User.active.is_(True))) or 0}

@app.get("/api/admin/users")
def admin_users(user: User = Depends(admin_required), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.created_at.desc()).limit(500)).all()
    return [{"id": x.id, "email": x.email, "display_name": x.display_name,
             "active": x.active, "is_admin": x.is_admin, "created_at": x.created_at.isoformat()} for x in rows]

@app.patch("/api/admin/users/{user_id}/toggle")
def admin_toggle_user(user_id: int, user: User = Depends(admin_required), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target: raise HTTPException(404, "User not found")
    if target.id == user.id: raise HTTPException(400, "You cannot suspend your own administrator account")
    target.active = not target.active
    audit(db, user.email, "admin.user.toggle", target.email)
    db.commit()
    return {"id": target.id, "active": target.active}

@app.get("/api/admin/audit")
def admin_audit(user: User = Depends(admin_required), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return [{"actor": x.actor, "action": x.action, "detail": x.detail, "created_at": x.created_at.isoformat()} for x in rows]
