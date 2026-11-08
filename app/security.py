import time
from collections import defaultdict, deque
import jwt
from pwdlib import PasswordHash
from fastapi import HTTPException, Request, status
from .config import settings

password_hash = PasswordHash.recommended()
_attempts = defaultdict(deque)

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except Exception:
        return False

def create_token(subject: str, role: str = "user") -> str:
    now = int(time.time())
    return jwt.encode({"sub": subject, "role": role, "iat": now,
                       "exp": now + settings.session_max_age_seconds},
                      settings.app_secret, algorithm="HS256")

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

def check_login_rate(request: Request):
    key = request.client.host if request.client else "unknown"
    now = time.time()
    q = _attempts[key]
    while q and q[0] < now - 300:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many login attempts. Try again later.")
    q.append(now)
