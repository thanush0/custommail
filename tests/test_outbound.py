from fastapi.testclient import TestClient
import app.main as main
from app.db import Base, engine


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_external_send_uses_mailumi_and_keeps_bcc_private(monkeypatch):
    captured = {}

    def fake_send(api_key, sender, to, subject, text, cc=None, bcc=None, idempotency_key=None):
        captured.update(api_key=api_key, sender=sender, to=list(to), cc=list(cc or []), bcc=list(bcc or []), idempotency_key=idempotency_key)
        return {"id": "mailumi-test-out-1", "status": "queued"}

    monkeypatch.setattr(main, "send_mailumi_email", fake_send)
    monkeypatch.setattr(main.settings, "mailumi_send_api_key", "test-send-key")

    with TestClient(main.app) as client:
        reg = client.post("/api/register", json={"username": "alice", "display_name": "Alice", "password": "correct-horse-battery"})
        assert reg.status_code == 200, reg.text
        response = client.post("/api/mail/messages", json={"to": ["recipient@example.net"], "cc": ["copy@example.net"], "bcc": ["hidden@example.net"], "subject": "Outbound test", "body": "Test body"})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "submitted_to_mailumi"
    assert result["provider_id"] == "mailumi-test-out-1"
    assert captured["api_key"] == "test-send-key"
    assert captured["to"] == ["recipient@example.net"]
    assert captured["cc"] == ["copy@example.net"]
    assert captured["bcc"] == ["hidden@example.net"]
    assert "hidden@example.net" not in captured["to"] + captured["cc"]
    assert captured["idempotency_key"] == f"custommail-message-{result['id']}"
