"""System performance measurements (offline: external search disabled).

    python -m eval.performance

Measures extraction time by format/size, per-stage pipeline latency by
document length, peak memory, and job throughput with concurrent jobs.
External search latency depends on third-party services and network and is
reported per analysis in each report's timings (and by eval/retrieval_live.py).
"""

import io
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="oc-perf-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP}/perf.db",
        "STORAGE_DIR": f"{_TMP}/files",
        "EMBEDDED_WORKER": "false",
        "EMBEDDING_PROVIDER": os.environ.get("EMBEDDING_PROVIDER", "wordvec"),
    }
)

import docx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services.extraction.sandbox import extract  # noqa: E402
from app.worker.queue import Worker, run_until_idle  # noqa: E402
from eval.similarity import _brown_files  # noqa: E402

RESULTS = Path(__file__).parent / "results"


def corpus_text(words: int, offset: int = 0) -> str:
    files = _brown_files()
    out, n = [], 0
    for name in sorted(files)[offset:]:
        for para in files[name].split("\n\n"):
            out.append(para)
            n += len(para.split())
            if n >= words:
                return "\n\n".join(out)
    return "\n\n".join(out)


def pdf_bytes(pages: int) -> bytes:
    text = corpus_text(pages * 450)
    paras = text.split("\n\n")
    story, per = [], max(1, len(paras) // pages)
    st = getSampleStyleSheet()["BodyText"]
    for i, p in enumerate(paras):
        story.append(Paragraph(p.replace("&", "&amp;").replace("<", "&lt;"), st))
        if i and i % per == 0:
            story.append(PageBreak())
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build(story)
    return buf.getvalue()


def docx_bytes(words: int) -> bytes:
    d = docx.Document()
    for p in corpus_text(words).split("\n\n"):
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def timed(fn, *a):
    t = time.perf_counter()
    r = fn(*a)
    return r, round(time.perf_counter() - t, 3)


def main() -> dict:
    results: dict = {"embedding_provider": os.environ["EMBEDDING_PROVIDER"], "extraction": {}, "pipeline": {}, "concurrency": {}}
    for pages in (20, 100):
        data = pdf_bytes(pages)
        r, secs = timed(extract, "doc.pdf", data)
        results["extraction"][f"pdf_{pages}_pages"] = {"bytes": len(data), "words": len(r.text.split()), "seconds": secs}
    data = docx_bytes(10000)
    r, secs = timed(extract, "doc.docx", data)
    results["extraction"]["docx_10k_words"] = {"bytes": len(data), "words": len(r.text.split()), "seconds": secs}

    app = create_app()
    with TestClient(app) as c:
        reg = c.post("/api/auth/register", json={"name": "Perf", "email": "perf@example.org", "password": "PerfTest2026x"}).json()
        c.headers["X-CSRF-Token"] = reg["csrf_token"]
        c.put("/api/settings", json={"external_search": False})
        # Library sources so the comparison stage has realistic work (10 x ~2,000 words).
        for i in range(10):
            c.post("/api/library", json={"title": f"Source {i}", "text": corpus_text(2000, offset=200 + i * 2)})
        for words in (1000, 5000, 20000):
            text = corpus_text(words, offset=100)
            t0 = time.perf_counter()
            r = c.post("/api/documents", data={"text": text})
            upload = time.perf_counter() - t0
            run_until_idle(600)
            job = c.get(f"/api/jobs/{r.json()['job']['id']}").json()
            rep = c.get(f"/api/reports/{job['report_id']}").json()
            results["pipeline"][f"{words}_words"] = {
                "upload_request_seconds": round(upload, 3),
                "stage_seconds": rep["timings"],
                "total_seconds": round(sum(rep["timings"].values()), 2),
            }
        # Concurrency: 6 jobs, 2 worker threads.
        ids = [c.post("/api/documents", data={"text": corpus_text(3000, offset=120 + k)}).json()["job"]["id"] for k in range(6)]
        w = Worker(threads=2)
        t0 = time.perf_counter()
        w.start()
        while True:
            statuses = [c.get(f"/api/jobs/{j}").json()["status"] for j in ids]
            if all(s in ("completed", "failed") for s in statuses):
                break
            time.sleep(0.5)
        wall = time.perf_counter() - t0
        w.stop()
        results["concurrency"] = {"jobs": 6, "worker_threads": 2, "words_each": 3000, "wall_seconds": round(wall, 2), "jobs_per_minute": round(6 / wall * 60, 1), "all_completed": all(s == "completed" for s in statuses)}
    results["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "performance.json").write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
