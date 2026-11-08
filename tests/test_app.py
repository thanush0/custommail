# Copyright (c) 2026 Thanush Priyan R.. All Rights Reserved.
# CustomMail | https://github.com/thanush0/custommail
import os
os.environ.setdefault("APP_SECRET","test-secret-that-is-long-enough-for-tests")
os.environ.setdefault("ADMIN_PASSWORD","test-admin-password-very-long")
os.environ.setdefault("ADMIN_USERNAME","admin")
os.environ.setdefault("DATABASE_URL","sqlite:////tmp/custommail-test-suite.db")
os.environ.setdefault("COOKIE_SECURE","false")
os.environ.setdefault("MAIL_DOMAIN","example.com")
os.environ.setdefault("STORAGE_DIR","/tmp/custommail-test-storage")

from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine

def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

def test_health():
    with TestClient(app) as c:
        r=c.get("/health")
        assert r.status_code==200 and r.json()["ok"]

def test_registration_and_private_mail():
    with TestClient(app) as a, TestClient(app) as b:
        ra=a.post("/api/register",json={"username":"alice","display_name":"Alice","password":"correct-horse-battery"})
        rb=b.post("/api/register",json={"username":"bob","display_name":"Bob","password":"another-secure-password"})
        assert ra.status_code==200 and rb.status_code==200
        sent=a.post("/api/mail/messages",json={"to":["bob@example.com"],"subject":"Hello","body":"Private message"})
        assert sent.status_code==200
        inbox=b.get("/api/mail/messages?folder=inbox")
        assert len(inbox.json())==1 and inbox.json()[0]["body"]=="Private message"
        assert a.get(f"/api/mail/messages/{inbox.json()[0]['id']}").status_code==404

def test_duplicate_registration_and_admin_auth():
    with TestClient(app) as c:
        assert c.post("/api/register",json={"username":"alice","password":"correct-horse-battery"}).status_code==200
        assert c.post("/api/register",json={"username":"alice","password":"correct-horse-battery"}).status_code==409
        assert c.get("/api/admin/users").status_code==403
