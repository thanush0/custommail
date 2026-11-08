import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .models import TempMailbox, Message, Attachment


def hash_temp_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def purge_temp_mailbox(db, mailbox: TempMailbox, storage_dir: str) -> int:
    rows = db.scalars(
        select(Message).where(Message.owner_email == mailbox.email)
    ).all()
    for row in rows:
        files = db.scalars(
            select(Attachment).where(Attachment.message_id == row.id)
        ).all()
        for item in files:
            path = Path(storage_dir) / "attachments" / item.storage_name
            path.unlink(missing_ok=True)
            db.delete(item)
        db.delete(row)
    mailbox.active = False
    return len(rows)


def cleanup_expired_temp_mailboxes(db, storage_dir: str, batch_size: int = 100) -> int:
    now = datetime.now(timezone.utc)
    total = 0
    while True:
        expired = db.scalars(
            select(TempMailbox)
            .where(TempMailbox.active.is_(True), TempMailbox.expires_at <= now)
            .order_by(TempMailbox.expires_at.asc())
            .limit(batch_size)
        ).all()
        if not expired:
            break
        for mailbox in expired:
            total += purge_temp_mailbox(db, mailbox, storage_dir)
        db.commit()
        if len(expired) < batch_size:
            break
    return total
