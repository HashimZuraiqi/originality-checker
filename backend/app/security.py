"""Authentication, sessions, CSRF protection, rate limiting and audit logging."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuditLog, AuthSession, User, utcnow

SESSION_COOKIE = "oc_session"
SESSION_DAYS = 14
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# scrypt parameters (~16 MiB, ~50 ms): memory-hard, standard-library only.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# A real hash to compare against when the email does not exist, so login
# timing does not reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(400, "Password must be at least 10 characters long.")
    if len(password) > 256:
        raise HTTPException(400, "Password is too long.")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise HTTPException(400, "Password must mix upper- and lower-case letters and include a number.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def create_session(db: Session, user: User, request: Request, response: Response) -> AuthSession:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(days=SESSION_DAYS),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(session)
    db.flush()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    return session


def destroy_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        found = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        if found:
            db.delete(found)
    response.delete_cookie(SESSION_COOKIE, path="/")


@dataclass
class CurrentUser:
    user: User
    session: AuthSession

    @property
    def id(self) -> str:
        return self.user.id


def load_session(db: Session, request: Request) -> AuthSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if session is None:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:  # SQLite drops tzinfo
        expires = expires.replace(tzinfo=utcnow().tzinfo)
    if expires < utcnow():
        db.delete(session)
        return None
    return session


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    session = load_session(db, request)
    if session is None:
        raise HTTPException(401, "Please sign in.")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    # CSRF: state-changing requests must echo the per-session token in a
    # header. A cross-site form cannot set custom headers, and SameSite=Lax
    # keeps the cookie off cross-site subrequests as a second layer.
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, session.csrf_token):
            raise HTTPException(403, "Security token missing or expired. Reload the page and try again.")
    return CurrentUser(user=user, session=session)


class RateLimiter:
    """In-process sliding-window limiter. Good enough for a single API
    instance; a multi-instance deployment should move this to Redis."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (now - q[0])) + 1
                raise HTTPException(429, f"Too many attempts. Try again in {retry} seconds.")
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


def audit(db: Session, action: str, user_id: str | None = None, target: str = "", ip: str = "", detail: str = "") -> None:
    db.add(AuditLog(user_id=user_id, action=action, target=target[:100], ip=ip[:64], detail=detail[:500]))


DEFAULT_PREFERENCES = {
    "retention_days": None,  # None -> server default
    "external_search": True,
    "llm_assist": True,
    "peer_comparison": True,
}


def preferences(user: User) -> dict:
    prefs = dict(DEFAULT_PREFERENCES)
    prefs.update(user.preferences or {})
    if prefs["retention_days"] is None:
        prefs["retention_days"] = get_settings().default_retention_days
    return prefs


def dummy_verify() -> None:
    verify_password("x", _DUMMY_HASH)
