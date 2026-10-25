# Setup and deployment

## 1. Quick start with Docker (recommended)

Requirements: Docker with Compose v2.24+.

```bash
git clone https://github.com/HashimZuraiqi/originality-checker.git
cd originality-checker
cp .env.example .env          # optional; sensible defaults work without it
docker compose up --build     # first build takes a few minutes
```

Open <http://localhost:8000>, create an account and upload a document.

This starts PostgreSQL, the web/API container and a separate worker container.
Data lives in the `pgdata` and `appdata` volumes (`docker compose down -v`
deletes them).

**Choosing the semantic model** (build argument `SEMANTIC`):

| Value | Model | Image size | Notes |
|---|---|---|---|
| `wordvec` (default) | SIF-weighted spaCy word vectors | ~0.8 GB | Works without Hugging Face; verified end-to-end |
| `sbert` | Sentence-Transformers all-MiniLM-L6-v2 + STS cross-encoder | ~2 GB | Best paraphrase detection; downloads models from Hugging Face at build time |
| `none` | TF-IDF fallback | ~0.6 GB | Semantic results marked "degraded" in reports |

```bash
SEMANTIC=sbert docker compose up --build
```

**Optional services**

- Grammar/spelling: `docker compose --profile grammar up` and set
  `LANGUAGETOOL_URL=http://languagetool:8010` in `.env`.
- Open-web search: set `BRAVE_API_KEY` (Brave Search API) or
  `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX` (Google Programmable Search).
- AI writing assistant: set `ANTHROPIC_API_KEY`.
- Set `CONTACT_EMAIL` so OpenAlex and Crossref route you to their faster
  "polite pool".

## 2. Local development (no Docker)

Requirements: Python 3.11+, Node 18+.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# optional, better semantic matching without Hugging Face:
pip install spacy==3.8.16 "en_core_web_md @ https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl"
python -m app.tools.build_wordvec
uvicorn app.main:app --reload          # http://127.0.0.1:8000 (SQLite + embedded worker)

# Frontend (in another terminal)
cd frontend
npm install
npm run dev                            # http://127.0.0.1:5173, proxies /api to :8000
```

To serve the built frontend from the API instead: `npm run build`, then open
<http://127.0.0.1:8000>.

**Tests and evaluation**

```bash
cd backend
pytest                                  # 89 tests, ~15 s, no network needed
ruff check .
python -m eval.ai_detection --train     # downloads Brown + ELLIPSE, retrains, writes results
python -m eval.similarity --all         # downloads MRPC + Brown, calibration + benchmark
python -m eval.performance              # latency, memory, concurrency
python -m eval.retrieval_live --n 20    # needs internet: real source-discovery success rate
cd ../frontend && npm run typecheck
```

## 3. Production checklist

1. **HTTPS**: put the app behind a reverse proxy (Caddy, nginx, Traefik) that
   terminates TLS. Set `COOKIE_SECURE=true`.
2. **`ENVIRONMENT=production`** and a strong `ENCRYPTION_KEY`, loaded from a
   secrets manager. Back the key up: without it, stored documents cannot be
   decrypted.
3. **PostgreSQL** (`DATABASE_URL=postgresql+psycopg2://…`) with backups.
4. **Workers**: `EMBEDDED_WORKER=false` on API replicas and one or more
   `python -m app.worker` processes (`WORKER_THREADS` each). Jobs are claimed
   with `FOR UPDATE SKIP LOCKED`, so workers scale horizontally.
5. **Shared storage**: all API and worker instances must see the same
   `STORAGE_DIR` (a shared volume, or adapt `app/storage.py` to S3-compatible
   object storage).
6. **Rate limiting** is per process; with several API replicas, add limits at
   the reverse proxy.
7. **Monitoring**: logs go to stdout (JSON-friendly format); the
   `/api/health` endpoint is used by the container health check. Watch job
   failure rates and provider error rates (both visible in reports).
8. **Retention**: set `DEFAULT_RETENTION_DAYS` to match your institution's
   policy; users can choose shorter periods.

## 4. Scaling notes

- An analysis spends most of its time in source discovery (network-bound, a
  few seconds to ~30 s depending on providers) and in semantic comparison
  (CPU-bound, roughly linear in the number of retrieved source sentences).
- The query budget (`MAX_QUERIES_PER_DOCUMENT`) and source cap
  (`MAX_SOURCES_PER_DOCUMENT`) bound both cost and latency per document.
- Search results and fetched pages are cached in the database, so repeated or
  similar documents re-use them.
- The `sbert` model benefits from more CPU cores; batch size is fixed at 64.
