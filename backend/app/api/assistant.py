import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..crypto import decrypt_text, encrypt_text
from ..db import get_db
from ..models import Revision
from ..security import CurrentUser, audit, client_ip, current_user, preferences, rate_limiter
from ..services.export import revised_docx, revised_pdf
from ..services.humanizer.llm import AssistantUnavailable, assistant_available, llm_rewrite
from ..services.humanizer.rules import MODES, rule_based_rewrite
from ..services.text import word_count
from .documents import get_owned_document
from .schemas import ExportTextIn, RevisionIn, RewriteIn, revision_out

router = APIRouter(prefix="/api", tags=["writing assistant"])


@router.get("/assistant/status")
def assistant_status(me: CurrentUser = Depends(current_user)):
    prefs = preferences(me.user)
    return {
        "llm_configured": assistant_available(),
        "llm_allowed": prefs["llm_assist"],
        "modes": MODES,
        "disclosure": "When the AI assistant is used, the selected text is sent to Anthropic's API to generate the rewrite. "
        "Anthropic does not train on API data by default. Turn this off in Settings to use only the on-server rule-based editor.",
    }


@router.post("/assistant/rewrite")
def rewrite(body: RewriteIn, request: Request, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rate_limiter.check(f"rewrite:{me.id}", limit=60, window_s=3600)
    if body.document_id:
        doc = get_owned_document(db, me, body.document_id)
        if doc.text_encrypted is None:
            raise HTTPException(409, "The document text is not available yet.")
        full = decrypt_text(doc.text_encrypted)
    elif body.text is not None:
        full = body.text
    else:
        raise HTTPException(400, "Provide text or a document.")
    start = body.start if body.start is not None else 0
    end = body.end if body.end is not None else len(full)
    if not (0 <= start < end <= len(full)):
        raise HTTPException(400, "The selected range is outside the text.")
    segment = full[start:end]
    if word_count(segment) < 3:
        raise HTTPException(400, "Select at least a sentence to rewrite.")
    if len(segment) > 60_000 and body.engine != "rules":
        raise HTTPException(413, "Select a shorter passage (about 8,000 words at most) for the AI assistant.")

    prefs = preferences(me.user)
    want_llm = body.engine in ("auto", "llm")
    notice = None
    result = None
    if want_llm and prefs["llm_assist"] and assistant_available():
        try:
            result = llm_rewrite(segment, body.mode)
            audit(db, "assistant_llm", me.id, body.document_id or "", client_ip(request), f"{body.mode}, {len(segment)} chars")
        except AssistantUnavailable as e:
            if body.engine == "llm":
                raise HTTPException(503, str(e)) from e
            notice = f"{e} The rule-based editor was used instead."
    elif body.engine == "llm":
        reason = "turned off in your settings" if not prefs["llm_assist"] else "not configured on this server"
        raise HTTPException(503, f"The AI writing assistant is {reason}.")
    elif want_llm:
        notice = (
            "The AI writing assistant is turned off in your settings, so the on-server rule-based editor was used."
            if not prefs["llm_assist"]
            else "The AI writing assistant is not configured on this server, so the rule-based editor was used."
        )
    if result is None:
        rb = rule_based_rewrite(segment, body.mode)
        result = {
            "engine": "rules",
            "model": None,
            "text": rb.text,
            "changes": [c.to_dict() for c in rb.changes],
            "explanations": rb.notes,
            "concerns": [],
        }
    # Map change offsets from the selection back to the full text.
    for c in result["changes"]:
        c["start"] += start
        c["end"] += start
    result["notice"] = notice
    result["selection"] = {"start": start, "end": end}
    return result


@router.post("/documents/{doc_id}/revisions", status_code=201)
def save_revision(doc_id: str, body: RevisionIn, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    rev = Revision(document_id=doc.id, user_id=me.id, label=body.label.strip() or "Revision", text_encrypted=encrypt_text(body.text), word_count=word_count(body.text))
    db.add(rev)
    db.flush()
    return revision_out(rev)


def _owned_revision(db: Session, me: CurrentUser, rev_id: str) -> Revision:
    rev = db.get(Revision, rev_id)
    if rev is None or rev.user_id != me.id:
        raise HTTPException(404, "Revision not found.")
    return rev


@router.get("/revisions/{rev_id}")
def get_revision(rev_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    rev = _owned_revision(db, me, rev_id)
    return {**revision_out(rev), "text": decrypt_text(rev.text_encrypted)}


@router.delete("/revisions/{rev_id}", status_code=204)
def delete_revision(rev_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_owned_revision(db, me, rev_id))
    return Response(status_code=204)


@router.post("/export/text")
def export_text(body: ExportTextIn, me: CurrentUser = Depends(current_user)):
    """Download text (e.g. the studio's current version) as DOCX, PDF or TXT."""
    safe = re.sub(r"[^\w\- ]", "_", body.title)[:60].strip() or "document"
    if body.format == "docx":
        return Response(
            revised_docx(body.title, body.text),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'},
        )
    if body.format == "pdf":
        return Response(revised_pdf(body.title, body.text), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})
    return Response(body.text, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}.txt"'})
