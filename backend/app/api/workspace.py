"""Collections (folders/assignments), private source library, settings,
account management and system status."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt_text, encrypt_text
from ..db import get_db
from ..models import AuditLog, Collection, Document, LibrarySource, User
from ..security import CurrentUser, audit, check_password_strength, client_ip, current_user, hash_password, preferences, verify_password
from ..services.discovery.providers import PROVIDER_CLASSES, provider_enabled
from ..services.extraction.parsers import ExtractionError
from ..services.extraction.sandbox import extract
from ..services.humanizer.llm import assistant_available
from ..services.similarity.embeddings import get_provider
from ..services.text import normalize_document, word_count
from ..storage import delete_file
from .documents import _read_limited
from .schemas import CollectionIn, LibraryTextIn, PreferencesIn, collection_out, library_out

router = APIRouter(prefix="/api", tags=["workspace"])


# ------------------------------------------------------------ collections
def _owned_collection(db: Session, me: CurrentUser, cid: str) -> Collection:
    c = db.get(Collection, cid)
    if c is None or c.user_id != me.id:
        raise HTTPException(404, "Collection not found.")
    return c


def _collection_settings(body: CollectionIn) -> dict:
    return {"min_match_words": body.min_match_words, "exclude_quotes": body.exclude_quotes, "exclude_bibliography": body.exclude_bibliography}


@router.get("/collections")
def list_collections(me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Document.collection_id, func.count()).where(Document.user_id == me.id).group_by(Document.collection_id)).all())
    cols = db.scalars(select(Collection).where(Collection.user_id == me.id).order_by(Collection.created_at.desc())).all()
    return [collection_out(c, counts.get(c.id, 0)) for c in cols]


@router.post("/collections", status_code=201)
def create_collection(body: CollectionIn, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = Collection(user_id=me.id, title=body.title.strip(), instructions=body.instructions.strip(), due_date=body.due_date, settings=_collection_settings(body))
    db.add(c)
    db.flush()
    return collection_out(c)


@router.get("/collections/{cid}")
def get_collection(cid: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = _owned_collection(db, me, cid)
    count = db.scalar(select(func.count()).where(Document.collection_id == c.id))
    return collection_out(c, count or 0)


@router.put("/collections/{cid}")
def update_collection(cid: str, body: CollectionIn, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = _owned_collection(db, me, cid)
    c.title, c.instructions, c.due_date, c.settings = body.title.strip(), body.instructions.strip(), body.due_date, _collection_settings(body)
    return collection_out(c)


@router.delete("/collections/{cid}", status_code=204)
def delete_collection(cid: str, request: Request, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    c = _owned_collection(db, me, cid)
    for d in db.scalars(select(Document).where(Document.collection_id == c.id)):
        delete_file(d.storage_key)
        db.delete(d)
    db.flush()  # documents first; the collection row goes after
    db.delete(c)
    audit(db, "collection_delete", me.id, cid, client_ip(request))
    return Response(status_code=204)


# ---------------------------------------------------------------- library
@router.get("/library")
def list_library(me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LibrarySource).where(LibrarySource.user_id == me.id).order_by(LibrarySource.created_at.desc())).all()
    return [library_out(s) for s in rows]


def _add_library(db: Session, me: CurrentUser, title: str, url: str, text: str) -> dict:
    text = normalize_document(text)
    n = word_count(text)
    if n < 20:
        raise HTTPException(400, "A library source needs at least 20 words of text.")
    s = LibrarySource(user_id=me.id, title=title.strip(), url=url, text_encrypted=encrypt_text(text), word_count=n)
    db.add(s)
    db.flush()
    return library_out(s)


@router.post("/library", status_code=201)
def add_library_text(body: LibraryTextIn, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return _add_library(db, me, body.title, body.url, body.text)


@router.post("/library/upload", status_code=201)
async def add_library_file(
    file: UploadFile = File(...),
    title: str = Form(default="", max_length=300),
    url: str = Form(default="", max_length=2000),
    me: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = await _read_limited(file, get_settings().max_upload_mb * 1024 * 1024)
    try:
        result = extract(file.filename or "source.txt", data)
    except ExtractionError as e:
        raise HTTPException(400, str(e)) from e
    if url and not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    return _add_library(db, me, title or (file.filename or "Source").rsplit(".", 1)[0], url, result.text)


@router.get("/library/{sid}")
def get_library(sid: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(LibrarySource, sid)
    if s is None or s.user_id != me.id:
        raise HTTPException(404, "Source not found.")
    return {**library_out(s), "text": decrypt_text(s.text_encrypted)}


@router.delete("/library/{sid}", status_code=204)
def delete_library(sid: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(LibrarySource, sid)
    if s is None or s.user_id != me.id:
        raise HTTPException(404, "Source not found.")
    db.delete(s)
    return Response(status_code=204)


# --------------------------------------------------------------- settings
@router.get("/settings")
def get_preferences(me: CurrentUser = Depends(current_user)):
    return {"preferences": preferences(me.user), "server_default_retention_days": get_settings().default_retention_days}


@router.put("/settings")
def update_preferences(body: PreferencesIn, request: Request, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    prefs = dict(user.preferences or {})
    for k, v in body.model_dump(exclude_none=True).items():
        prefs[k] = v
    user.preferences = prefs
    audit(db, "settings_update", me.id, ip=client_ip(request), detail=",".join(body.model_dump(exclude_none=True)))
    return {"preferences": preferences(user)}


@router.post("/account/password")
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    user = db.get(User, me.id)
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(400, "Your current password is incorrect.")
    check_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    audit(db, "password_change", me.id, ip=client_ip(request))
    return {"ok": True}


@router.delete("/account", status_code=204)
def delete_account(request: Request, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Permanently delete the account and every document, report and file."""
    for d in db.scalars(select(Document).where(Document.user_id == me.id)):
        delete_file(d.storage_key)
    user = db.get(User, me.id)
    db.delete(user)  # cascades (ON DELETE CASCADE) to everything the user owns
    db.add(AuditLog(user_id=None, action="account_delete", target=me.id, ip=client_ip(request)))
    response = Response(status_code=204)
    response.delete_cookie("oc_session", path="/")
    return response


@router.get("/account/activity")
def account_activity(me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.user_id == me.id).order_by(AuditLog.created_at.desc()).limit(100)).all()
    return [{"action": r.action, "target": r.target, "ip": r.ip, "detail": r.detail, "at": r.created_at.isoformat()} for r in rows]


# ----------------------------------------------------------------- system
@router.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@router.get("/system/status", tags=["system"])
def system_status(me: CurrentUser = Depends(current_user)):
    settings = get_settings()
    providers = []
    for cls in PROVIDER_CLASSES:
        p = cls(settings, None)  # type: ignore[arg-type]
        providers.append(
            {
                "name": cls.name,
                "label": cls.label,
                "kind": cls.kind,
                "enabled": provider_enabled(settings, cls.name),
                "configured": p.configured(),
                "requires_key": cls.requires_key,
            }
        )
    emb = get_provider()
    return {
        "providers": providers,
        "embedding": emb.describe(),
        "assistant_configured": assistant_available(),
        "grammar_engine": "LanguageTool + built-in rules" if settings.languagetool_url else "built-in rules",
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_document_words": settings.max_document_words,
            "max_pdf_pages": settings.max_pdf_pages,
        },
    }
