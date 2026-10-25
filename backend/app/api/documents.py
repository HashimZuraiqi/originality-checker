import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt_json, decrypt_text, encrypt_bytes, encrypt_text
from ..db import get_db
from ..models import Collection, Document, Job, Report, Revision, utcnow
from ..security import CurrentUser, audit, client_ip, current_user, rate_limiter
from ..services.export import report_docx, report_pdf
from ..services.extraction.parsers import ExtractionError, detect_type
from ..services.text import normalize_document, word_count
from ..storage import delete_file, save_file
from ..worker.queue import enqueue
from .schemas import AnalysisOptions, document_out, job_out, revision_out

router = APIRouter(prefix="/api", tags=["documents"])


def get_owned_document(db: Session, me: CurrentUser, doc_id: str) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None or doc.user_id != me.id:
        raise HTTPException(404, "Document not found.")
    return doc


def _safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name).strip() or "upload"
    return name[:200]


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/documents", status_code=201)
async def create_document(
    request: Request,
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    title: str | None = Form(default=None, max_length=300),
    author_name: str = Form(default="", max_length=200),
    collection_id: str | None = Form(default=None),
    options: str | None = Form(default=None),
    me: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Upload a file (PDF, DOCX, TXT) or paste text; analysis starts automatically."""
    settings = get_settings()
    rate_limiter.check(f"upload:{me.id}", limit=30, window_s=3600)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > (settings.max_upload_mb + 1) * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than the {settings.max_upload_mb} MB limit.")
    try:
        opts = AnalysisOptions(**(json.loads(options) if options else {})).model_dump()
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"Invalid analysis options: {e}") from e

    collection = None
    if collection_id:
        collection = db.get(Collection, collection_id)
        if collection is None or collection.user_id != me.id:
            raise HTTPException(404, "Collection not found.")

    if file is not None and file.filename:
        data = await _read_limited(file, settings.max_upload_mb * 1024 * 1024)
        if not data:
            raise HTTPException(400, "The file is empty.")
        filename = _safe_filename(file.filename)
        try:
            file_type = detect_type(filename, data)
        except ExtractionError as e:
            raise HTTPException(400, str(e)) from e
        doc = Document(
            user_id=me.id,
            title=(title or filename.rsplit(".", 1)[0]).strip()[:300] or "Untitled document",
            author_name=author_name.strip(),
            filename=filename,
            file_type=file_type,
            size_bytes=len(data),
            storage_key=save_file(encrypt_bytes(data)),
            collection_id=collection.id if collection else None,
        )
    elif text and text.strip():
        clean = normalize_document(text)
        if len(clean) > 2_000_000:
            raise HTTPException(413, "The pasted text is too long.")
        if word_count(clean) < 20:
            raise HTTPException(400, "Please paste at least 20 words.")
        first_line = clean.split("\n", 1)[0][:80]
        doc = Document(
            user_id=me.id,
            title=(title or first_line or "Pasted text").strip()[:300],
            author_name=author_name.strip(),
            file_type="text",
            size_bytes=len(clean.encode()),
            text_encrypted=encrypt_text(clean),
            word_count=word_count(clean),
            collection_id=collection.id if collection else None,
        )
    else:
        raise HTTPException(400, "Upload a file or paste some text.")

    if collection and collection.due_date:
        due = collection.due_date if collection.due_date.tzinfo else collection.due_date.replace(tzinfo=utcnow().tzinfo)
        doc.late = utcnow() > due
    db.add(doc)
    db.flush()
    job = enqueue(db, doc, me.id, opts)
    audit(db, "document_upload", me.id, doc.id, client_ip(request), f"{doc.file_type}, {doc.size_bytes} bytes")
    return {"document": document_out(doc), "job": job_out(job)}


@router.get("/documents")
def list_documents(
    q: str | None = Query(default=None, max_length=200),
    collection_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    me: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Document).where(Document.user_id == me.id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Document.title).like(like), func.lower(Document.author_name).like(like), func.lower(Document.filename).like(like)))
    if collection_id:
        stmt = stmt.where(Document.collection_id == collection_id)
    if status:
        stmt = stmt.where(Document.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    docs = db.scalars(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [document_out(d) for d in docs]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    job = db.get(Job, doc.latest_job_id) if doc.latest_job_id else None
    reports = db.scalars(select(Report).where(Report.document_id == doc.id).order_by(Report.created_at.desc())).all()
    revisions = db.scalars(select(Revision).where(Revision.document_id == doc.id).order_by(Revision.created_at.desc())).all()
    return {
        "document": document_out(doc),
        "job": job_out(job) if job else None,
        "reports": [{"id": r.id, "job_id": r.job_id, "summary": r.summary, "created_at": r.created_at.isoformat()} for r in reports],
        "revisions": [revision_out(r) for r in revisions],
    }


@router.get("/documents/{doc_id}/text")
def get_document_text(doc_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    if doc.text_encrypted is None:
        raise HTTPException(409, "The text is not available yet; wait for extraction to finish.")
    return {"text": decrypt_text(doc.text_encrypted)}


@router.post("/documents/{doc_id}/analyze", status_code=201)
def reanalyze(doc_id: str, request: Request, options: AnalysisOptions | None = None, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    active = db.scalar(select(Job).where(Job.document_id == doc.id, Job.status.in_(("queued", "running"))))
    if active:
        raise HTTPException(409, "An analysis of this document is already running.")
    job = enqueue(db, doc, me.id, (options or AnalysisOptions()).model_dump())
    audit(db, "document_reanalyze", me.id, doc.id, client_ip(request))
    return {"document": document_out(doc), "job": job_out(job)}


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, request: Request, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    for job in db.scalars(select(Job).where(Job.document_id == doc.id, Job.status.in_(("queued", "running")))):
        job.cancel_requested = True
    delete_file(doc.storage_key)
    db.delete(doc)
    audit(db, "document_delete", me.id, doc_id, client_ip(request))
    return Response(status_code=204)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.user_id != me.id:
        raise HTTPException(404, "Job not found.")
    report = db.scalar(select(Report.id).where(Report.job_id == job.id))
    return {**job_out(job), "report_id": report}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None or job.user_id != me.id:
        raise HTTPException(404, "Job not found.")
    if job.status in ("completed", "failed", "cancelled"):
        raise HTTPException(409, f"The analysis has already {job.status}.")
    job.cancel_requested = True
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = utcnow()
        job.error = "Cancelled by the user."
        doc = db.get(Document, job.document_id)
        if doc and doc.latest_job_id == job.id:
            doc.status = "cancelled"
    return job_out(job)


def _owned_report(db: Session, me: CurrentUser, report_id: str) -> tuple[Report, Document]:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found.")
    doc = get_owned_document(db, me, report.document_id)
    return report, doc


@router.get("/reports/{report_id}")
def get_report(report_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    report, _doc = _owned_report(db, me, report_id)
    return {"id": report.id, "created_at": report.created_at.isoformat(), **decrypt_json(report.data_encrypted)}


@router.get("/documents/{doc_id}/report")
def latest_report(doc_id: str, me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, me, doc_id)
    report = db.scalar(select(Report).where(Report.document_id == doc.id).order_by(Report.created_at.desc()).limit(1))
    if report is None:
        raise HTTPException(404, "No report yet.")
    return {"id": report.id, "created_at": report.created_at.isoformat(), **decrypt_json(report.data_encrypted)}


@router.get("/reports/{report_id}/export")
def export_report(report_id: str, request: Request, format: str = Query(pattern="^(pdf|docx)$"), me: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    report, doc = _owned_report(db, me, report_id)
    data = decrypt_json(report.data_encrypted)
    safe = re.sub(r"[^\w\- ]", "_", doc.title)[:60].strip() or "report"
    audit(db, "report_export", me.id, report.id, client_ip(request), format)
    if format == "pdf":
        return Response(report_pdf(data), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe} - integrity report.pdf"'})
    return Response(
        report_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe} - integrity report.docx"'},
    )
