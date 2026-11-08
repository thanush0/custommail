import hashlib
import html
import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from .models import User, Message, Attachment, AuditLog

def ingest_email(db, raw, envelope_to, domain, storage_dir, max_attachment_bytes):
    parsed=BytesParser(policy=policy.default).parsebytes(raw)
    sender=parseaddr(str(parsed.get("From","")))[1].strip().lower()
    if not sender or "@" not in sender or len(sender)>255:
        raise ValueError("Invalid sender address")
    recipients={a.strip().lower() for _,a in getaddresses([envelope_to]) if a and "@" in a}
    recipients={r for r in recipients if r.rsplit("@",1)[1]==domain.lower()}
    if not recipients: raise ValueError("No valid recipient for this mail domain")
    subject=str(parsed.get("Subject",""))[:250]
    mid=str(parsed.get("Message-ID","")).strip()
    mid=mid[:255] if mid else "sha256:"+hashlib.sha256(raw).hexdigest()
    bodies=[]; html_bodies=[]; files=[]; total=0
    for part in parsed.walk():
        if part.is_multipart(): continue
        filename=part.get_filename()
        if filename or part.get_content_disposition()=="attachment":
            data=part.get_payload(decode=True) or b""
            total+=len(data)
            if len(files)>=10 or len(data)>max_attachment_bytes or total>20*1024*1024:
                raise ValueError("Attachment limit exceeded")
            safe=re.sub(r"[^A-Za-z0-9._-]","_",Path((filename or "attachment").replace("\\\\","/")).name)[:180] or "attachment"
            files.append((safe,part.get_content_type()[:120],data))
        elif part.get_content_type()=="text/plain":
            try: bodies.append(str(part.get_content()))
            except (LookupError,UnicodeError):
                bodies.append((part.get_payload(decode=True) or b"").decode("utf-8","replace"))
        elif part.get_content_type()=="text/html":
            try: html_bodies.append(str(part.get_content()))
            except (LookupError,UnicodeError):
                html_bodies.append((part.get_payload(decode=True) or b"").decode("utf-8","replace"))
    body="\n\n".join(bodies)[:500000]
    if not body and html_bodies:
        html_body="\n".join(html_bodies)
        html_body=re.sub(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", html_body)
        body=html.unescape(re.sub("<[^>]+>", " ", html_body))
        body=re.sub(r"[ \t]+", " ", body).strip()[:500000]
    storage=Path(storage_dir)/"attachments"; storage.mkdir(parents=True,exist_ok=True)
    created=[]; stored=0; duplicates=0
    try:
        for recipient in sorted(recipients):
            user=db.scalar(select(User).where(User.email==recipient,User.active.is_(True)))
            if not user: continue
            exists=db.scalar(select(Message.id).where(Message.owner_email==recipient,Message.message_id==mid))
            if exists: duplicates+=1; continue
            row=Message(owner_email=recipient,sender=sender,recipients=recipient,subject=subject,
                body_text=body,body_html="",folder="inbox",delivery_status="received",message_id=mid)
            db.add(row); db.flush()
            for filename,ctype,data in files:
                name=uuid4().hex; path=storage/name; path.write_bytes(data); created.append(path)
                db.add(Attachment(message_id=row.id,filename=filename,content_type=ctype,
                    storage_name=name,size_bytes=len(data)))
            db.add(AuditLog(actor="inbound-gateway",action="mail.received",
                detail=f"recipient={recipient}; sender={sender}"))
            stored+=1
        if stored==0 and duplicates==0: raise ValueError("No active mailbox found for recipient")
        db.commit()
        return {"accepted":True,"stored":stored,"duplicates":duplicates}
    except Exception:
        db.rollback()
        for path in created: path.unlink(missing_ok=True)
        raise
