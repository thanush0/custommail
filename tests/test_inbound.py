# Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
# CustomMail | https://github.com/thanush0/custommail
import os

os.environ.setdefault("INBOUND_EMAIL_SECRET", "test-inbound-secret")

from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _register(client):
    return client.post(
        "/api/register",
        json={"username": "alice", "display_name": "Alice",
              "password": "correct-horse-battery"},
    )


def _raw_email():
    return (
        b"From: sender@example.net\r\n"
        b"To: alice@example.com\r\n"
        b"Subject: OTP test\r\n"
        b"Message-ID: <otp-1@example.net>\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Your OTP is 123456"
    )


def test_inbound_email_delivers_to_registered_inbox():
    with TestClient(app) as client:
        assert _register(client).status_code == 200
        response = client.post(
            "/api/inbound/email",
            content=_raw_email(),
            headers={
                "X-Inbound-Secret": "test-inbound-secret",
                "X-Envelope-To": "alice@example.com",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "accepted": True, "stored": 1, "duplicates": 0
        }

        inbox = client.get("/api/mail/messages?folder=inbox")
        assert inbox.status_code == 200
        assert len(inbox.json()) == 1
        assert inbox.json()[0]["sender"] == "sender@example.net"
        assert inbox.json()[0]["subject"] == "OTP test"
        assert inbox.json()[0]["body"] == "Your OTP is 123456"


def test_inbound_email_duplicate_is_not_stored_twice():
    with TestClient(app) as client:
        assert _register(client).status_code == 200
        headers = {
            "X-Inbound-Secret": "test-inbound-secret",
            "X-Envelope-To": "alice@example.com",
        }
        first = client.post("/api/inbound/email", content=_raw_email(), headers=headers)
        second = client.post("/api/inbound/email", content=_raw_email(), headers=headers)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["stored"] == 0
        assert second.json()["duplicates"] == 1
        assert len(client.get("/api/mail/messages?folder=inbox").json()) == 1


def test_inbound_email_rejects_invalid_gateway_secret():
    with TestClient(app) as client:
        response = client.post(
            "/api/inbound/email",
            content=_raw_email(),
            headers={
                "X-Inbound-Secret": "wrong-secret",
                "X-Envelope-To": "alice@example.com",
            },
        )
        assert response.status_code == 401


def test_html_only_email_is_readable_and_scripts_are_removed():
    with TestClient(app) as client:
        assert _register(client).status_code == 200
        raw = (
            b"From: sender@example.net\r\n"
            b"To: alice@example.com\r\n"
            b"Subject: HTML only\r\n"
            b"Message-ID: <html-only-1@example.net>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            b"<html><body><p>Welcome <b>Alice</b></p>"
            b"<script>alert(hidden-script)</script>"
            b"<style>.secret{display:none}</style></body></html>"
        )
        response = client.post("/api/inbound/email", content=raw, headers={"X-Inbound-Secret": "test-inbound-secret", "X-Envelope-To": "alice@example.com"})
        assert response.status_code == 200, response.text
        inbox = client.get("/api/mail/messages?folder=inbox").json()
        assert len(inbox) == 1
        body = inbox[0]["body"]
        assert "Welcome Alice" in body
        assert "hidden-script" not in body
        assert ".secret" not in body


def _signed_mailumi_request(event):
    import os
    import base64
    import hashlib
    import hmac
    import json
    import time

    raw = json.dumps(event, separators=(',', ':')).encode()
    event_id = event['id']
    timestamp = str(int(time.time()))
    secret = os.environ['MAILUMI_WEBHOOK_SECRET']
    key = base64.b64decode(secret.removeprefix('whsec_'), validate=True)
    signed = f'{event_id}.{timestamp}.'.encode() + raw
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode('ascii')

    return raw, {
        'webhook-id': event_id,
        'webhook-timestamp': timestamp,
        'webhook-signature': f'v1,{signature}',
    }


def test_mailumi_rejects_invalid_signature():
    import time

    with TestClient(app) as client:
        response = client.post(
            '/api/webhooks/mailumi',
            content=b'{}',
            headers={
                'webhook-id': 'evt-invalid-test',
                'webhook-timestamp': str(int(time.time())),
                'webhook-signature': 'v1,invalid-signature',
            },
        )
        assert response.status_code == 401


def test_mailumi_valid_event_is_saved_once():
    with TestClient(app) as client:
        assert _register(client).status_code == 200

        event = {
            'id': 'evt-custommail-test-001',
            'type': 'email.received',
            'created_at': '2026-11-08T12:00:00.000Z',
            'data': {
                'email_id': 'mailumi-email-test-001',
                'from': 'Mailumi Test <sender@example.net>',
                'to': ['alice@example.com'],
                'cc': [],
                'subject': 'Mailumi OTP test',
                'text': 'Your verification code is 483921',
                'html': '',
                'message_id': '<mailumi-test-001@example.net>',
                'spam': False,
                'attachments': [],
            },
        }

        raw, headers = _signed_mailumi_request(event)
        first = client.post('/api/webhooks/mailumi', content=raw, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()['stored'] == 1

        second = client.post('/api/webhooks/mailumi', content=raw, headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()['duplicate'] is True

        inbox = client.get('/api/mail/messages?folder=inbox')
        assert inbox.status_code == 200
        assert len(inbox.json()) == 1
        assert inbox.json()[0]['subject'] == 'Mailumi OTP test'
        assert inbox.json()[0]['body'] == 'Your verification code is 483921'


def test_mailumi_html_only_email_removes_scripts():
    with TestClient(app) as client:
        assert _register(client).status_code == 200
        event = {
            'id': 'evt-html-test-002',
            'type': 'email.received',
            'data': {
                'email_id': 'mailumi-html-002',
                'from': 'sender@example.net',
                'to': ['alice@example.com'],
                'cc': [],
                'subject': 'HTML email',
                'text': '',
                'html': '<p>Welcome Alice</p><script>hidden()</script><style>.secret{}</style>',
                'message_id': '<html-002@example.net>',
                'spam': False,
                'attachments': [],
            },
        }
        raw, headers = _signed_mailumi_request(event)
        response = client.post('/api/webhooks/mailumi', content=raw, headers=headers)
        assert response.status_code == 200, response.text
        body = client.get('/api/mail/messages?folder=inbox').json()[0]['body']
        assert 'Welcome Alice' in body
        assert 'hidden()' not in body
        assert '.secret' not in body


def test_mailumi_unknown_recipient_is_not_marked_processed():
    with TestClient(app) as client:
        event = {
            'id': 'evt-unknown-test-003',
            'type': 'email.received',
            'data': {
                'email_id': 'mailumi-unknown-003',
                'from': 'sender@example.net',
                'to': ['nobody@example.com'],
                'cc': [],
                'subject': 'Unknown recipient',
                'text': 'Test',
                'html': '',
                'message_id': '<unknown-003@example.net>',
                'spam': False,
                'attachments': [],
            },
        }
        raw, headers = _signed_mailumi_request(event)
        response = client.post('/api/webhooks/mailumi', content=raw, headers=headers)
        assert response.status_code == 400
        assert 'No active mailbox' in response.text
