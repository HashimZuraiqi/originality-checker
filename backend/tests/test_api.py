import io
from datetime import timedelta

import docx
import pytest

from app.db import session_scope
from app.models import Document, Job, utcnow
from app.worker import queue
from app.worker.queue import purge_expired_documents, recover_stale_jobs, run_until_idle

from . import fakeweb
from .conftest import register

ESSAY = (
    "Plants and the energy of life\n\n"
    "During photosynthesis in green plants, light energy is captured and used to convert water, carbon dioxide, and minerals "
    "into oxygen and energy-rich organic compounds. This process is the foundation of almost every food chain on Earth.\n\n"
    'As one encyclopedia puts it, "it would be impossible to overestimate the importance of photosynthesis in the maintenance '
    'of life on Earth" (Britannica, 2020).\n\n'
    "My own view is that biology lessons should spend more time on how farmers and gardeners can apply these ideas in practice, "
    "because the topic is often taught in an abstract way that students forget quickly after the exam.\n\n"
    "References\n\nBritannica. (2020). Photosynthesis. Encyclopaedia Britannica.\n"
)


def upload_text(client, text=ESSAY, **form):
    r = client.post("/api/documents", data={"text": text, **form})
    assert r.status_code == 201, r.text
    return r.json()


def analyse(client, text=ESSAY, **form):
    created = upload_text(client, text, **form)
    run_until_idle()
    job = client.get(f"/api/jobs/{created['job']['id']}").json()
    assert job["status"] == "completed", job
    report = client.get(f"/api/reports/{job['report_id']}").json()
    return created, job, report


# ------------------------------------------------------------------ auth
def test_register_login_logout_and_me(client):
    me = register(client)
    assert client.get("/api/auth/me").json()["user"]["email"] == me["email"]
    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/session").json() == {"authenticated": False}
    r = client.post("/api/auth/login", json={"email": me["email"], "password": "CorrectHorse9Battery"})
    assert r.status_code == 200


def test_weak_password_and_duplicate_email_rejected(client):
    assert client.post("/api/auth/register", json={"name": "A", "email": "weak@example.org", "password": "short"}).status_code == 400
    assert client.post("/api/auth/register", json={"name": "A", "email": "weak@example.org", "password": "alllowercase123"}).status_code == 400
    register(client)
    me = client.get("/api/auth/me").json()
    r = client.post("/api/auth/register", json={"name": "B", "email": me["user"]["email"], "password": "CorrectHorse9Battery"})
    assert r.status_code == 409


def test_wrong_password_same_message_and_rate_limited(client):
    me = register(client)
    client.post("/api/auth/logout")
    msgs = set()
    for _ in range(8):
        r = client.post("/api/auth/login", json={"email": me["email"], "password": "WrongPassword1"})
        assert r.status_code == 401
        msgs.add(r.json()["detail"])
    r2 = client.post("/api/auth/login", json={"email": "nobody@example.org", "password": "WrongPassword1"})
    msgs.add(r2.json()["detail"])
    assert len(msgs) == 1  # no account enumeration
    assert client.post("/api/auth/login", json={"email": me["email"], "password": "WrongPassword1"}).status_code == 429


def test_csrf_required_for_state_changes(client, user):
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/documents", data={"text": ESSAY}).status_code == 403
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/collections", json={"title": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/collections", json={"title": "x"}).status_code == 201


def test_session_cookie_flags(client):
    r = client.post("/api/auth/register", json={"name": "C", "email": "cookie@example.org", "password": "CorrectHorse9Battery"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


# ------------------------------------------------------- authorization
def test_users_cannot_access_each_others_data(client):
    register(client, "Alice")
    created, job, report = analyse(client)
    doc_id = created["document"]["id"]
    register(client, "Bob")  # switches the client's session to Bob
    for path in (f"/api/documents/{doc_id}", f"/api/documents/{doc_id}/text", f"/api/reports/{report['id']}", f"/api/jobs/{job['id']}", f"/api/reports/{report['id']}/export?format=pdf"):
        assert client.get(path).status_code == 404, path
    assert client.delete(f"/api/documents/{doc_id}").status_code == 404
    assert client.post("/api/assistant/rewrite", json={"document_id": doc_id, "mode": "concise"}).status_code == 404
    assert client.get("/api/documents").json()["total"] == 0


def test_unauthenticated_requests_rejected(client):
    for path in ("/api/documents", "/api/collections", "/api/library", "/api/settings", "/api/system/status"):
        assert client.get(path).status_code == 401


# ---------------------------------------------------------- pipeline
def test_full_pipeline_finds_sources_and_builds_report(client, user):
    created, job, report = analyse(client)
    assert [s["status"] for s in job["stages"]] == ["done"] * 9
    s = report["summary"]
    assert s["similarity_index"] > 0 and s["unattributed_pct"] > 0
    cats = {f["category"] for f in report["similarity"]["findings"]}
    assert "unattributed" in cats and "quoted_cited" in cats
    wiki = [r for r in report["similarity"]["sources"] if "Wikipedia" in r["title"]]
    assert wiki and wiki[0]["url"].endswith("/wiki/Photosynthesis")
    f = next(f for f in report["similarity"]["findings"] if f["category"] == "unattributed")
    assert report["text"][f["start"] : f["end"]].startswith("During photosynthesis")
    assert report["coverage"]["external_search"] is True
    assert any(q["family"] == "phrase" for q in report["queries"])
    assert report["document"]["language"] == "en"
    assert set(report["timings"]) >= {"extract", "discover", "compare", "quality"}  # the report stage cannot time itself
    doc = client.get(f"/api/documents/{created['document']['id']}").json()
    assert doc["document"]["status"] == "completed" and doc["document"]["summary"]["similarity_index"] == s["similarity_index"]


def test_external_search_off_sends_nothing_and_flags_gap(client, user):
    client.put("/api/settings", json={"external_search": False})
    _, _, report = analyse(client)
    assert fakeweb.state["requests"] == []
    assert report["summary"]["coverage_gap"] is True
    assert report["coverage"]["external_search"] is False


def test_docx_upload_and_exports(client, user):
    d = docx.Document()
    for para in ESSAY.split("\n\n"):
        d.add_paragraph(para)
    buf = io.BytesIO()
    d.save(buf)
    r = client.post("/api/documents", files={"file": ("essay.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"title": "Docx essay"})
    assert r.status_code == 201
    run_until_idle()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["status"] == "completed"
    pdf = client.get(f"/api/reports/{job['report_id']}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    word = client.get(f"/api/reports/{job['report_id']}/export?format=docx")
    assert word.status_code == 200 and word.content.startswith(b"PK")


def test_upload_validation(client, user):
    assert client.post("/api/documents", files={"file": ("x.exe", b"MZ\x90", "application/octet-stream")}).status_code == 400
    assert client.post("/api/documents", data={"text": "too short"}).status_code == 400
    assert client.post("/api/documents", data={}).status_code == 400
    assert client.post("/api/documents", data={"text": ESSAY, "options": '{"min_match_words": 2}'}).status_code == 400


def test_corrupt_pdf_fails_with_message_not_retried(client, user):
    r = client.post("/api/documents", files={"file": ("bad.pdf", b"%PDF-1.4 corrupt", "application/pdf")})
    run_until_idle()
    job = client.get(f"/api/jobs/{r.json()['job']['id']}").json()
    assert job["status"] == "failed" and "damaged" in job["error"] and job["attempts"] == 1
    assert job["stages"][0]["status"] == "failed"


def test_cancel_queued_job_and_rerun(client, user):
    created = upload_text(client)
    jid = created["job"]["id"]
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "cancelled"
    run_until_idle()
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "cancelled"
    r = client.post(f"/api/documents/{created['document']['id']}/analyze", json={})
    assert r.status_code == 201
    assert client.post(f"/api/documents/{created['document']['id']}/analyze", json={}).status_code == 409  # already queued
    run_until_idle()
    assert client.get(f"/api/jobs/{r.json()['job']['id']}").json()["status"] == "completed"


def test_cancel_running_job_stops_pipeline(client, user, monkeypatch):
    created = upload_text(client)
    jid = created["job"]["id"]
    from app.services.discovery import engine

    real = engine.discover_sources

    def cancelling(*args, **kwargs):
        with session_scope() as db:
            db.get(Job, jid).cancel_requested = True
        return real(*args, **kwargs)

    monkeypatch.setattr("app.worker.pipeline.discover_sources", cancelling)
    run_until_idle()
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "cancelled"
    assert any(s["status"] == "skipped" for s in job["stages"])


def test_crash_is_retried_once_then_fails(client, user, monkeypatch):
    created = upload_text(client)
    calls = {"n": 0}

    def boom(job_id):
        calls["n"] += 1
        raise RuntimeError("unexpected")

    monkeypatch.setattr(queue, "run_pipeline", boom)
    run_until_idle()
    job = client.get(f"/api/jobs/{created['job']['id']}").json()
    assert calls["n"] == 2 and job["status"] == "failed" and "unexpected error" in job["error"]


def test_stale_running_job_recovered(client, user):
    created = upload_text(client)
    with session_scope() as db:
        j = db.get(Job, created["job"]["id"])
        j.status, j.attempts, j.heartbeat_at = "running", 1, utcnow() - timedelta(hours=1)
    assert recover_stale_jobs() == 1
    assert client.get(f"/api/jobs/{created['job']['id']}").json()["status"] == "queued"


def test_retention_purges_old_documents(client, user):
    client.put("/api/settings", json={"retention_days": 7})
    created = upload_text(client)
    with session_scope() as db:
        db.get(Document, created["document"]["id"]).created_at = utcnow() - timedelta(days=8)
    assert purge_expired_documents() >= 1
    assert client.get(f"/api/documents/{created['document']['id']}").status_code == 404


# ---------------------------------------------------- collections etc.
def test_collection_peer_comparison_resubmission_and_late_flag(client, user):
    col = client.post("/api/collections", json={"title": "Essay 1", "due_date": (utcnow() - timedelta(days=1)).isoformat()}).json()
    peer_text = (
        "Original thoughts about rivers\n\nRivers carve valleys over millions of years by slowly removing sediment from their beds "
        "and carrying it downstream toward the sea, where it settles into broad deltas that shift with every flood season.\n\n"
        "I find this slow, patient work of water one of the most beautiful processes in geology."
    )
    a = upload_text(client, peer_text, collection_id=col["id"], author_name="Sam")
    run_until_idle()
    assert a["document"]["late"] is True
    # Same author resubmits: own draft is not a match.
    _, _, own = analyse(client, peer_text, collection_id=col["id"], author_name=" sam ")
    assert not any(s["kind"] == "peer" for s in own["similarity"]["sources"])
    # Another student copies Sam.
    _, _, copied = analyse(client, peer_text, collection_id=col["id"], author_name="Jordan")
    peers = [s for s in copied["similarity"]["sources"] if s["kind"] == "peer"]
    assert peers and "sam" in peers[0]["title"].lower() and copied["summary"]["similarity_index"] > 50
    listed = client.get(f"/api/documents?collection_id={col['id']}").json()
    assert listed["total"] == 3
    assert client.delete(f"/api/collections/{col['id']}").status_code == 204
    assert client.get("/api/documents").json()["total"] == 0


def test_library_source_is_compared(client, user):
    client.put("/api/settings", json={"external_search": False})
    src = (
        "Glaciers are slow-moving rivers of ice that form where snowfall exceeds melting for many years, "
        "compressing the snow into dense ice that flows downhill under its own weight."
    )
    assert client.post("/api/library", json={"title": "Course reader p. 12", "text": src}).status_code == 201
    _, _, report = analyse(client, "My essay\n\n" + src + " That is what my reading said, and I believe it completely.")
    assert report["similarity"]["sources"][0]["kind"] == "library"
    assert report["summary"]["similarity_index"] > 50


def test_assistant_rules_fallback_and_selection_offsets(client, user):
    status = client.get("/api/assistant/status").json()
    assert status["llm_configured"] is False
    text = "Intro sentence stays. It is important to note that we utilize tools in order to win."
    start = text.index("It is")
    r = client.post("/api/assistant/rewrite", json={"text": text, "mode": "concise", "start": start, "end": len(text)})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "rules" and "not configured" in body["notice"]
    assert all(c["start"] >= start for c in body["changes"])
    assert client.post("/api/assistant/rewrite", json={"text": text, "mode": "concise", "engine": "llm"}).status_code == 503


def test_revisions_and_text_export(client, user):
    created, _, _ = analyse(client)
    doc_id = created["document"]["id"]
    rev = client.post(f"/api/documents/{doc_id}/revisions", json={"text": "Revised text here.", "label": "v2"}).json()
    assert client.get(f"/api/revisions/{rev['id']}").json()["text"] == "Revised text here."
    for fmt, magic in (("docx", b"PK"), ("pdf", b"%PDF"), ("txt", b"Revised")):
        r = client.post("/api/export/text", json={"title": "T", "text": "Revised text here.", "format": fmt})
        assert r.status_code == 200 and r.content.startswith(magic)
    assert client.delete(f"/api/revisions/{rev['id']}").status_code == 204


def test_account_deletion_removes_everything(client, user):
    created, _, _ = analyse(client)
    assert client.delete("/api/account").status_code == 204
    with session_scope() as db:
        assert db.get(Document, created["document"]["id"]) is None
    assert client.get("/api/auth/me").status_code == 401


def test_stored_text_is_encrypted(client, user):
    created = upload_text(client)
    with session_scope() as db:
        blob = db.get(Document, created["document"]["id"]).text_encrypted
    assert b"photosynthesis" not in blob.lower()


def test_system_status_reports_capabilities(client, user):
    st = client.get("/api/system/status").json()
    names = {p["name"]: p for p in st["providers"]}
    assert names["brave"]["configured"] is False and names["openalex"]["configured"] is True
    assert st["embedding"]["name"] == "lexical" and st["embedding"]["degraded"] is True


@pytest.mark.parametrize("path", ["/", "/history", "/documents/abc"])
def test_spa_routes_served_when_built(client, path):
    r = client.get(path)
    assert r.status_code in (200, 404)  # 404 only if the frontend has not been built
