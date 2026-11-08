# Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
# CustomMail | https://github.com/thanush0/custommail
import os, base64, hashlib, hmac, json, time
from datetime import datetime, timedelta, timezone
os.environ.setdefault("APP_SECRET", "test-secret-that-is-long-enough-for-tests")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password-very-long")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/custommail-test-suite.db")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("MAIL_DOMAIN", "example.com")
os.environ.setdefault("STORAGE_DIR", "/tmp/custommail-test-storage")
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.config import settings
from app.db import Base, engine, SessionLocal
from app.models import TempMailbox, Message

def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

def create_inbox(client):
    response = client.post("/api/temp-mailboxes")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["email"].endswith("@example.com") and len(data["token"]) >= 40
    return data

def signed_event(event, secret):
    raw = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    signed = f"{event['id']}.{timestamp}.".encode() + raw
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return raw, {"webhook-id": event["id"], "webhook-timestamp": timestamp,
                 "webhook-signature": f"v1,{signature}"}

def test_temp_mail_page_is_public():
    with TestClient(app) as client:
        response = client.get("/temp-mail")
        assert response.status_code == 200 and "DISPOSABLE INBOX" in response.text

def test_temp_mail_creation_token_protection_and_expiry():
    with TestClient(app) as client:
        inbox = create_inbox(client)
        assert client.get("/api/temp-mailboxes/current").status_code == 401
        auth = {"Authorization": f"Bearer {inbox['token']}"}
        response = client.get("/api/temp-mailboxes/current", headers=auth)
        assert response.status_code == 200 and response.json()["email"] == inbox["email"]
        db = SessionLocal()
        try:
            row = db.scalar(select(TempMailbox).where(TempMailbox.email == inbox["email"]))
            assert row is not None and row.token_hash != inbox["token"]
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        finally:
            db.close()
        expired = client.get("/api/temp-mailboxes/current", headers=auth)
        assert expired.status_code == 410
        db = SessionLocal()
        try:
            row = db.scalar(select(TempMailbox).where(TempMailbox.email == inbox["email"]))
            assert row.active is False
        finally:
            db.close()

def test_mailumi_webhook_delivers_and_delete_revokes_temp_inbox():
    old_secret = settings.mailumi_webhook_secret
    settings.mailumi_webhook_secret = "whsec_dGVzdC1tYWlsdW1pLXdlYmhvb2stc2VjcmV0"
    try:
        with TestClient(app) as client:
            inbox = create_inbox(client)
            event = {"id": "evt-temp-mail-test-001", "type": "email.received", "data": {
                "email_id": "mailumi-temp-email-001", "from": "sender@example.net",
                "to": [inbox["email"]], "cc": [], "subject": "Temporary OTP",
                "text": "Your code is 739251", "html": "", "message_id": "<temp-001@example.net>",
                "spam": False, "attachments": []}}
            raw, headers = signed_event(event, settings.mailumi_webhook_secret)
            delivered = client.post("/api/webhooks/mailumi", content=raw, headers=headers)
            assert delivered.status_code == 200, delivered.text
            assert delivered.json()["stored"] == 1
            auth = {"Authorization": f"Bearer {inbox['token']}"}
            messages = client.get("/api/temp-mailboxes/current/messages", headers=auth)
            assert messages.status_code == 200 and len(messages.json()) == 1
            item = messages.json()[0]
            assert item["subject"] == "Temporary OTP"
            opened = client.get(f"/api/temp-mailboxes/current/messages/{item['id']}", headers=auth)
            assert opened.status_code == 200 and opened.json()["body"] == "Your code is 739251"
            deleted = client.delete("/api/temp-mailboxes/current", headers=auth)
            assert deleted.status_code == 200 and deleted.json()["deleted_messages"] == 1
            assert client.get("/api/temp-mailboxes/current", headers=auth).status_code == 410
    finally:
        settings.mailumi_webhook_secret = old_secret
