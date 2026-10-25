"""Database-backed job queue and worker loop.

Why not Redis/Celery: a job here is a few seconds to a few minutes of work,
volume is modest, and the database already gives durable state, atomic
claiming (SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL) and an audit
trail. One less service to run and secure. The worker can run inside the
API process (EMBEDDED_WORKER=true, default for local use) or as separate
processes (`python -m app.worker`) for production.
"""

import logging
import os
import socket
import threading
import time
import traceback
from datetime import timedelta

from sqlalchemy import select, update

from ..config import get_settings
from ..db import session_scope
from ..models import Document, Job, User, utcnow
from ..security import preferences
from ..storage import delete_file
from .pipeline import JobCancelled, UserFacingError, initial_stages, run_pipeline

log = logging.getLogger(__name__)
_claim_lock = threading.Lock()


def enqueue(db, document: Document, user_id: str, options: dict | None = None) -> Job:
    job = Job(document_id=document.id, user_id=user_id, options=options or {}, stages=initial_stages(), status="queued")
    db.add(job)
    db.flush()
    document.latest_job_id = job.id
    document.status = "queued"
    return job


def claim_next(worker_id: str) -> str | None:
    settings = get_settings()
    with _claim_lock, session_scope() as db:
        q = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
        if not settings.is_sqlite:
            q = q.with_for_update(skip_locked=True)
        job = db.scalars(q).first()
        if job is None:
            return None
        # Conditional update guards against a concurrent claim.
        res = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="running", worker_id=worker_id, started_at=utcnow(), heartbeat_at=utcnow(), attempts=Job.attempts + 1)
        )
        if res.rowcount != 1:
            return None
        doc = db.get(Document, job.document_id)
        if doc:
            doc.status = "analyzing"
        return job.id


def _finish(job_id: str, status: str, error: str | None = None) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.error = error
        job.finished_at = utcnow()
        if status != "completed":
            stages = [dict(s) for s in job.stages]
            for s in stages:
                if s["status"] == "running":
                    s["status"] = "failed" if status == "failed" else "skipped"
                    if status == "cancelled":
                        s["detail"] = "Cancelled"
            job.stages = stages
        else:
            job.progress = 1.0
        doc = db.get(Document, job.document_id)
        if doc and doc.latest_job_id == job.id:
            doc.status = status


def process(job_id: str) -> None:
    started = time.monotonic()
    try:
        run_pipeline(job_id)
        _finish(job_id, "completed")
        log.info("job %s completed in %.1fs", job_id, time.monotonic() - started)
    except JobCancelled:
        _finish(job_id, "cancelled", "Cancelled by the user.")
    except UserFacingError as e:
        _finish(job_id, "failed", str(e))
    except Exception:  # noqa: BLE001 - worker must survive anything
        log.error("job %s crashed:\n%s", job_id, traceback.format_exc())
        settings = get_settings()
        retry = False
        with session_scope() as db:
            job = db.get(Job, job_id)
            retry = job is not None and job.attempts < settings.max_job_attempts and not job.cancel_requested
            if retry:
                job.status = "queued"
                job.stages = initial_stages()
                job.progress = 0.0
                job.error = "An unexpected error occurred; retrying automatically."
        if not retry:
            _finish(job_id, "failed", "An unexpected error occurred while analysing this document. Please try again.")


def recover_stale_jobs() -> int:
    """Requeue jobs whose worker stopped sending heartbeats (crash/restart)."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(seconds=settings.job_heartbeat_timeout_s)
    n = 0
    with session_scope() as db:
        for job in db.scalars(select(Job).where(Job.status == "running")):
            hb = job.heartbeat_at
            if hb is not None and hb.tzinfo is None:
                hb = hb.replace(tzinfo=cutoff.tzinfo)
            if hb is None or hb < cutoff:
                n += 1
                if job.attempts < settings.max_job_attempts and not job.cancel_requested:
                    job.status, job.stages, job.progress = "queued", initial_stages(), 0.0
                else:
                    job.status, job.error, job.finished_at = "failed", "The analysis stopped unexpectedly.", utcnow()
    return n


def purge_expired_documents() -> int:
    """Retention: delete documents older than each user's retention period."""
    n = 0
    with session_scope() as db:
        for user in db.scalars(select(User)):
            days = preferences(user)["retention_days"]
            if not days:
                continue
            cutoff = utcnow() - timedelta(days=int(days))
            for doc in db.scalars(select(Document).where(Document.user_id == user.id, Document.created_at < cutoff)):
                delete_file(doc.storage_key)
                db.delete(doc)
                n += 1
    return n


class Worker:
    def __init__(self, threads: int = 2):
        self.threads = threads
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self.ident = f"{socket.gethostname()}-{os.getpid()}"

    def _loop(self, n: int) -> None:
        wid = f"{self.ident}-{n}"
        while not self._stop.is_set():
            try:
                job_id = claim_next(wid)
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                job_id = None
            if job_id:
                process(job_id)
            else:
                self._stop.wait(1.0)

    def _maintenance(self) -> None:
        last_purge = 0.0
        while not self._stop.is_set():
            try:
                recover_stale_jobs()
                if time.monotonic() - last_purge > 3600:
                    purged = purge_expired_documents()
                    if purged:
                        log.info("retention: deleted %d expired documents", purged)
                    last_purge = time.monotonic()
            except Exception:  # noqa: BLE001
                log.exception("maintenance failed")
            self._stop.wait(60)

    def start(self) -> None:
        for n in range(self.threads):
            t = threading.Thread(target=self._loop, args=(n,), daemon=True, name=f"worker-{n}")
            t.start()
            self._workers.append(t)
        m = threading.Thread(target=self._maintenance, daemon=True, name="worker-maintenance")
        m.start()
        self._workers.append(m)

    def stop(self) -> None:
        self._stop.set()


def run_until_idle(max_seconds: float = 120) -> None:
    """Process queued jobs synchronously (tests, CLI)."""
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        job_id = claim_next("sync")
        if not job_id:
            return
        process(job_id)
