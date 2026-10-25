from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import (
    CurrentUser,
    audit,
    check_password_strength,
    client_ip,
    create_session,
    current_user,
    destroy_session,
    dummy_verify,
    hash_password,
    preferences,
    rate_limiter,
    verify_password,
)
from .schemas import LoginIn, RegisterIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me(user: User, csrf: str) -> dict:
    return {"user": {"id": user.id, "name": user.name, "email": user.email}, "csrf_token": csrf, "preferences": preferences(user)}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limiter.check(f"register:{client_ip(request)}", limit=10, window_s=3600)
    check_password_strength(body.password)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), preferences={})
    db.add(user)
    db.flush()
    session = create_session(db, user, request, response)
    audit(db, "register", user.id, ip=client_ip(request))
    return _me(user, session.csrf_token)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    email = body.email.lower()
    rate_limiter.check(f"login-ip:{ip}", limit=20, window_s=600)
    rate_limiter.check(f"login-email:{email}", limit=8, window_s=600)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        dummy_verify()
        raise HTTPException(401, "Email or password is incorrect.")
    if not verify_password(body.password, user.password_hash):
        audit(db, "login_failed", user.id, ip=ip)
        raise HTTPException(401, "Email or password is incorrect.")
    session = create_session(db, user, request, response)
    audit(db, "login", user.id, ip=ip)
    return _me(user, session.csrf_token)


@router.post("/logout")
def logout(request: Request, response: Response, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    destroy_session(db, request, response)
    audit(db, "logout", me.id, ip=client_ip(request))
    return {"ok": True}


@router.get("/me")
def me_endpoint(me: CurrentUser = Depends(current_user)):
    return _me(me.user, me.session.csrf_token)
