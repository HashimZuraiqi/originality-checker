# Originality Checker

An academic-integrity and writing platform. Upload one document (PDF, Word or
text) and get an explainable report: overlap with automatically discovered
sources, possible paraphrase, citation problems, AI-writing indicators with
honest uncertainty, and writing-quality feedback. Then improve the text in a
side-by-side writing studio and export the report and the revised document.

> Similarity is evidence, not proof of plagiarism. AI-writing indicators are
> not proof of AI use. The system is built to support a human reviewer, and it
> says plainly what it could and could not check.

## Quick start

```bash
docker compose up --build        # then open http://localhost:8000
```

Or without Docker, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Create an
account, drop a document on the page, and the analysis starts automatically.

## What it does

| Capability | How | Status |
|---|---|---|
| Text extraction | PDF (pypdf), DOCX (python-docx), TXT, pasted text; sandboxed child process with memory/CPU/time limits | Implemented, tested (malicious-file tests included) |
| Automatic source discovery | Distinctive-phrase, keyword and reference-title queries to OpenAlex, Crossref, Semantic Scholar, arXiv and Wikipedia; optional Brave or Google web search with a licensed key; open-access full text retrieval with robots.txt; MinHash de-duplication | Implemented and tested against recorded API formats. **Live retrieval could not be measured in the development sandbox** (outbound access blocked); run `python -m eval.retrieval_live` |
| Exact / near-exact overlap | 5-word fingerprints with gap-tolerant chaining; each word attributed to one source | Implemented; 100% recall on benchmark copies, span IoU ≈ 0.9 |
| Paraphrase detection | Sentence embeddings (Sentence-Transformers, or offline word vectors), topicality filter, sequence alignment, optional cross-encoder | Implemented; **moderate accuracy, reported separately and labelled by evidence strength** (see evaluation) |
| Citation analysis | APA/Harvard, narrative, IEEE, MLA; reference list parsing; missing/uncited references; uncited quotations; DOI verification via Crossref | Implemented, tested |
| AI-writing indicators | 12 stylometric features, sign-constrained logistic regression, banded verdict with an inconclusive band, paragraph-level results, caveats | Implemented; validated on human academic prose, learner essays and LLM text (details and limits in evaluation) |
| Writing quality | Readability, long sentences, passive voice, articles, confusions, spacing, wordiness; optional LanguageTool | Implemented, tested |
| Writing studio ("AI Humanizer") | Six modes; Claude (Anthropic API) or on-server rule-based editor; per-change accept/reject; protected citations, quotes and numbers; versions and compare | Implemented. Claude path tested against a simulated API (no key in the sandbox); rule-based path tested end-to-end |
| Reports | Interactive viewer with colour + underline-style highlights, filters, navigation, source comparison, coverage statement; PDF and Word export | Implemented, browser-tested |
| Collections, library, history | Assignments with due dates and peer comparison, private reference library, searchable history | Implemented, tested |
| Security and privacy | Encryption at rest, scrypt, CSRF, CSP, SSRF protection, rate limits, retention, audit log | Implemented, tested |

## Documentation

- [Architecture and design decisions](docs/ARCHITECTURE.md)
- [Evaluation methodology and results](docs/EVALUATION.md)
- [Known limitations](docs/LIMITATIONS.md)
- [Security and privacy](docs/SECURITY.md)
- [API reference](docs/API.md) (live OpenAPI at `/api/docs`)
- [Setup and deployment](docs/DEPLOYMENT.md)
- [Graduation-project defence: questions and answers](docs/DEFENSE.md)

## Tech stack

FastAPI · SQLAlchemy (PostgreSQL or SQLite) · database-backed job queue ·
pypdf · python-docx · NumPy · wordfreq · langdetect · optional
Sentence-Transformers / spaCy vectors · Anthropic Claude (optional) ·
ReportLab · React 18 · TypeScript · Tailwind CSS · Vite · Docker.

## Tests

```bash
cd backend && pytest        # 89 tests, no network needed
cd frontend && npm run typecheck
```
