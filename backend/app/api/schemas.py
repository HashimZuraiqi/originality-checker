"""Request bodies (validated by FastAPI/pydantic) and serializers."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..models import Collection, Document, Job, LibrarySource, Revision


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PreferencesIn(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    external_search: bool | None = None
    llm_assist: bool | None = None
    peer_comparison: bool | None = None


class AnalysisOptions(BaseModel):
    min_match_words: int = Field(default=8, ge=5, le=100)
    exclude_quotes: bool = True
    exclude_bibliography: bool = True
    external_search: bool = True
    semantic: bool = True


class CollectionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=5000)
    due_date: datetime | None = None
    min_match_words: int = Field(default=8, ge=5, le=100)
    exclude_quotes: bool = True
    exclude_bibliography: bool = True


class LibraryTextIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(default="", max_length=2000)
    text: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("url")
    @classmethod
    def http_only(cls, v: str) -> str:
        v = v.strip()
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class RewriteIn(BaseModel):
    mode: str = Field(pattern="^(natural|academic|professional|simple|concise|detailed)$")
    engine: str = Field(default="auto", pattern="^(auto|llm|rules)$")
    text: str | None = Field(default=None, max_length=200_000)
    document_id: str | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class RevisionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    label: str = Field(default="", max_length=200)


class ExportTextIn(BaseModel):
    title: str = Field(default="Revised document", max_length=300)
    text: str = Field(min_length=1, max_length=2_000_000)
    format: str = Field(default="docx", pattern="^(docx|pdf|txt)$")


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def document_out(d: Document) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "author_name": d.author_name,
        "collection_id": d.collection_id,
        "filename": d.filename,
        "file_type": d.file_type,
        "size_bytes": d.size_bytes,
        "word_count": d.word_count,
        "language": d.language,
        "status": d.status,
        "late": d.late,
        "latest_job_id": d.latest_job_id,
        "summary": d.latest_summary,
        "created_at": iso(d.created_at),
    }


def job_out(j: Job) -> dict:
    return {
        "id": j.id,
        "document_id": j.document_id,
        "status": j.status,
        "stages": j.stages,
        "progress": j.progress,
        "error": j.error,
        "attempts": j.attempts,
        "cancel_requested": j.cancel_requested,
        "created_at": iso(j.created_at),
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
    }


def collection_out(c: Collection, count: int = 0) -> dict:
    s = c.settings or {}
    return {
        "id": c.id,
        "title": c.title,
        "instructions": c.instructions,
        "due_date": iso(c.due_date),
        "min_match_words": s.get("min_match_words", 8),
        "exclude_quotes": s.get("exclude_quotes", True),
        "exclude_bibliography": s.get("exclude_bibliography", True),
        "document_count": count,
        "created_at": iso(c.created_at),
    }


def library_out(s: LibrarySource) -> dict:
    return {"id": s.id, "title": s.title, "url": s.url, "word_count": s.word_count, "created_at": iso(s.created_at)}


def revision_out(r: Revision) -> dict:
    return {"id": r.id, "document_id": r.document_id, "label": r.label, "word_count": r.word_count, "created_at": iso(r.created_at)}
