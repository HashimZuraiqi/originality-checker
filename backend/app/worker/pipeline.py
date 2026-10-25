"""The analysis pipeline: one function per stage, run in order for a job.

Progress shown to the user is the list of these stages with their real
status; nothing is simulated.
"""

import logging
import time
from collections.abc import Callable

from sqlalchemy import select

from ..config import get_settings
from ..crypto import decrypt_bytes, decrypt_text, encrypt_json, encrypt_text
from ..db import session_scope
from ..models import Collection, Document, Job, LibrarySource, Report, User, utcnow
from ..security import preferences
from ..services.aidetect.detector import analyze_ai_writing
from ..services.citations import analyze_citations
from ..services.discovery.engine import discover_sources
from ..services.extraction.parsers import ExtractionError
from ..services.extraction.sandbox import extract
from ..services.language import detect_language
from ..services.quality import analyze_quality
from ..services.similarity.embeddings import get_provider, get_reranker
from ..services.similarity.engine import analyze_similarity
from ..services.similarity.types import SourceDoc
from ..services.text import all_sentences, normalize_document, segment, word_count
from ..storage import read_file

log = logging.getLogger(__name__)

STAGES = [
    ("extract", "Extracting text"),
    ("segment", "Cleaning and segmenting"),
    ("language", "Identifying language"),
    ("citations", "Finding citations and references"),
    ("discover", "Searching for sources"),
    ("compare", "Comparing text (exact and semantic)"),
    ("ai", "Analysing AI-writing indicators"),
    ("quality", "Evaluating writing quality"),
    ("report", "Building the report"),
]


class JobCancelled(Exception):
    pass


class UserFacingError(Exception):
    """An error caused by the input (not retried)."""


def initial_stages() -> list[dict]:
    return [{"key": k, "label": label, "status": "pending", "detail": "", "progress": 0.0} for k, label in STAGES]


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self._last_write = 0.0
        self.timings: dict[str, float] = {}

    def _update(self, fn: Callable[[Job], None], force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < 0.5:
            return
        self._last_write = now
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                raise JobCancelled()
            if job.cancel_requested:
                raise JobCancelled()
            fn(job)
            job.heartbeat_at = utcnow()

    def check_cancel(self) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job is None or job.cancel_requested:
                raise JobCancelled()

    def stage(self, key: str, status: str, detail: str = "", progress: float | None = None, force: bool = True) -> None:
        def apply(job: Job) -> None:
            stages = [dict(s) for s in job.stages]
            done = 0
            for s in stages:
                if s["key"] == key:
                    s["status"] = status
                    if detail:
                        s["detail"] = detail[:300]
                    if progress is not None:
                        s["progress"] = round(progress, 3)
                    if status == "running" and not s.get("started_at"):
                        s["started_at"] = utcnow().isoformat()
                    if status in ("done", "skipped", "failed"):
                        s["finished_at"] = utcnow().isoformat()
                        s["progress"] = 1.0
                if s["status"] in ("done", "skipped"):
                    done += 1
                elif s["status"] == "running":
                    done += s.get("progress", 0.0)
            job.stages = stages
            job.progress = round(done / len(stages), 3)

        self._update(apply, force=force)


def run_pipeline(job_id: str) -> None:
    ctx = JobContext(job_id)
    settings = get_settings()
    with session_scope() as db:
        job = db.get(Job, job_id)
        doc = db.get(Document, job.document_id)
        user = db.get(User, job.user_id)
        prefs = preferences(user)
        options = dict(job.options or {})
        collection = db.get(Collection, doc.collection_id) if doc.collection_id else None
        doc_meta = {
            "id": doc.id,
            "title": doc.title,
            "author": doc.author_name,
            "file_type": doc.file_type,
            "filename": doc.filename,
            "storage_key": doc.storage_key,
            "user_id": doc.user_id,
            "collection_id": doc.collection_id,
            "text_encrypted": doc.text_encrypted,
        }
        if collection:
            options = {**(collection.settings or {}), **options}

    def timed(key: str):
        class _T:
            def __enter__(self_inner):
                self_inner.t = time.monotonic()
                ctx.stage(key, "running")

            def __exit__(self_inner, exc_type, exc, tb):
                ctx.timings[key] = round(time.monotonic() - self_inner.t, 3)
                return False

        return _T()

    # 1. Extract
    warnings: list[str] = []
    pages = None
    with timed("extract"):
        if doc_meta["text_encrypted"] is not None:
            raw_text = decrypt_text(doc_meta["text_encrypted"])
            ctx.stage("extract", "done", "Text was pasted directly")
        else:
            data = decrypt_bytes(read_file(doc_meta["storage_key"]))
            try:
                result = extract(doc_meta["filename"] or f"upload.{doc_meta['file_type']}", data)
            except ExtractionError as e:
                ctx.stage("extract", "failed", str(e))
                raise UserFacingError(str(e)) from e
            raw_text, pages, warnings = result.text, result.pages, result.warnings
            ctx.stage("extract", "done", f"{doc_meta['file_type'].upper()}" + (f", {pages} pages" if pages else ""))

    # 2. Segment
    with timed("segment"):
        text = normalize_document(raw_text)
        words = word_count(text)
        if words < 20:
            ctx.stage("segment", "failed", "Too little text")
            raise UserFacingError("The document contains fewer than 20 words, which is too little to analyse.")
        if words > settings.max_document_words:
            ctx.stage("segment", "failed", "Document too long")
            raise UserFacingError(f"The document has {words:,} words; the limit is {settings.max_document_words:,}. Split it into parts.")
        paragraphs = segment(text)
        sentences = all_sentences(paragraphs)
        with session_scope() as db:
            d = db.get(Document, doc_meta["id"])
            d.word_count = words
            if d.text_encrypted is None:
                d.text_encrypted = encrypt_text(text)
        ctx.stage("segment", "done", f"{words:,} words, {len(paragraphs)} paragraphs, {len(sentences)} sentences")

    # 3. Language
    with timed("language"):
        lang = detect_language(text)
        with session_scope() as db:
            db.get(Document, doc_meta["id"]).language = lang["code"]
        ctx.stage("language", "done", f"{lang['name']} ({round(lang['confidence'] * 100)}% confidence)")

    # 4. Citations
    with timed("citations"):
        citations = analyze_citations(text, sentences)
        ctx.stage("citations", "done", f"{len(citations.citations)} citations, {len(citations.references)} references")

    # 5. Discover
    with timed("discover"):
        sources: list[SourceDoc] = []
        with session_scope() as db:
            for lib in db.scalars(select(LibrarySource).where(LibrarySource.user_id == doc_meta["user_id"])):
                sources.append(SourceDoc(id=f"lib-{lib.id}", title=lib.title, text=decrypt_text(lib.text_encrypted), kind="library", provider="your library", url=lib.url or None))
            peer_note = ""
            if prefs["peer_comparison"] and doc_meta["collection_id"]:
                peers = db.scalars(
                    select(Document).where(
                        Document.collection_id == doc_meta["collection_id"],
                        Document.id != doc_meta["id"],
                        Document.text_encrypted.is_not(None),
                    )
                ).all()
                for peer in peers:
                    # Resubmissions by the same author are drafts, not plagiarism.
                    if doc_meta["author"] and peer.author_name.strip().lower() == doc_meta["author"].strip().lower():
                        continue
                    sources.append(
                        SourceDoc(
                            id=f"peer-{peer.id}",
                            title=f"Another submission: {peer.author_name or 'unnamed'} — {peer.title}",
                            text=decrypt_text(peer.text_encrypted),
                            kind="peer",
                            provider="this collection",
                        )
                    )
                peer_note = f", {len(peers)} collection documents"

        def discover_progress(msg: str, frac: float | None = None) -> None:
            ctx.stage("discover", "running", msg, frac, force=False)

        external_ok = prefs["external_search"] and options.get("external_search", True)
        discovery = discover_sources(
            text, paragraphs, sentences, citations, lang["code"] or "en",
            external_search=external_ok, progress=discover_progress, cancel_check=ctx.check_cancel,
        )
        sources.extend(discovery.sources)
        ctx.stage("discover", "done", f"{len(discovery.sources)} external sources{peer_note}, {sum(1 for s in sources if s.kind == 'library')} library documents")

    # 6. Compare
    with timed("compare"):
        provider = get_provider() if options.get("semantic", True) else None
        reranker = get_reranker() if provider and provider.name == "sbert" else None
        ctx.stage("compare", "running", f"Comparing against {len(sources)} sources" + (f" with {provider.label}" if provider else ""))
        similarity = analyze_similarity(
            text,
            sentences,
            citations,
            sources,
            provider,
            reranker,
            {
                "min_match_words": options.get("min_match_words", 8),
                "exclude_quotes": options.get("exclude_quotes", True),
                "exclude_bibliography": options.get("exclude_bibliography", True),
            },
            cancel_check=ctx.check_cancel,
        )
        ctx.stage("compare", "done", f"{len(similarity['findings'])} matched passages from {len(similarity['sources'])} sources")

    # 7. AI indicators
    with timed("ai"):
        ai = analyze_ai_writing(text, paragraphs, lang["code"] or "en", citations.bibliography_start)
        ctx.stage("ai", "done", ai["label"])

    # 8. Quality
    with timed("quality"):
        quality = analyze_quality(text, paragraphs, sentences, citations, lang["code"])
        ctx.stage("quality", "done", f"Quality index {quality['score']}")

    # 9. Report
    with timed("report"):
        citation_findings = [
            {"kind": "citation", "id": f"cit-{i + 1}", "category": iss["type"], "severity": iss["severity"], "start": iss["start"], "end": iss["end"], "message": iss["message"]}
            for i, iss in enumerate(citations.issues)
            if iss["end"] > iss["start"]
        ]
        ai_findings = [
            {
                "kind": "ai",
                "id": f"ai-{i + 1}",
                "category": "ai_indicators",
                "severity": "info",
                "start": p["start"],
                "end": p["end"],
                "message": "This paragraph shows several AI-associated style indicators: " + ", ".join(p["top_indicators"] or ["combined weak signals"]) + ". Not proof of AI use.",
            }
            for i, p in enumerate(ai.get("paragraphs", []))
            if p["verdict"] == "many_indicators"
        ]
        refs_payload = []
        for r in citations.references:
            refs_payload.append(
                {"index": r.index, "start": r.start, "end": r.end, "text": r.text, "number": r.number, "first_author": r.first_author, "year": r.year, "doi": r.doi, "url": r.url, "title": r.title_guess, "cited": r.cited, "verification": r.verification}
            )
        summary = {
            "similarity_index": similarity["scores"]["similarity_index"],
            "unattributed_pct": similarity["scores"]["unattributed_pct"],
            "semantic_pct": similarity["scores"]["semantic_pct"],
            "quoted_pct": similarity["scores"]["quoted_pct"],
            "ai_verdict": ai["verdict"],
            "ai_label": ai["label"],
            "quality_score": quality["score"],
            "citation_issues": len([i for i in citations.issues if i["severity"] in ("medium", "high")]),
            "source_count": len(similarity["sources"]),
            "sources_compared": len(sources),
            "coverage_gap": not discovery.coverage.get("external_search") or discovery.coverage.get("sources_compared", 0) == 0,
        }
        report = {
            "version": 2,
            "generated_at": utcnow().isoformat(),
            "document": {
                "id": doc_meta["id"],
                "title": doc_meta["title"],
                "author": doc_meta["author"],
                "words": words,
                "language": lang["code"],
                "language_name": lang["name"],
                "language_confidence": lang["confidence"],
                "file_type": doc_meta["file_type"],
                "pages": pages,
                "extraction_warnings": warnings,
            },
            "text": text,
            "summary": summary,
            "similarity": similarity,
            "ai": ai,
            "quality": quality,
            "citations": {
                "style": citations.style,
                "citations": [{"start": c.start, "end": c.end, "text": c.text, "style": c.style, "reference_index": c.reference_index} for c in citations.citations],
                "references": refs_payload,
                "issues": citations.issues,
                "bibliography_start": citations.bibliography_start,
            },
            "findings": {"citation": citation_findings, "ai": ai_findings},
            "coverage": discovery.coverage,
            "queries": discovery.queries,
            "settings": {k: options.get(k) for k in ("min_match_words", "exclude_quotes", "exclude_bibliography")},
            "timings": ctx.timings,
        }
        with session_scope() as db:
            db.add(Report(document_id=doc_meta["id"], job_id=job_id, summary=summary, data_encrypted=encrypt_json(report)))
            d = db.get(Document, doc_meta["id"])
            d.latest_summary = summary
        ctx.stage("report", "done", "Report ready")
