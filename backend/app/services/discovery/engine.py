"""Automatic source discovery: queries -> providers -> candidates -> full
text -> de-duplicated SourceDocs, plus an honest coverage statement."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import httpx

from ...config import get_settings
from ..citations import CitationContext
from ..similarity.minhash import near_duplicate_groups, shingle_set, signature
from ..similarity.types import SourceDoc
from ..text import Paragraph, Sentence, tokenize
from .http import ProviderClient, ProviderError
from .providers import PROVIDER_CLASSES, Candidate, CrossrefProvider, OpenAlexProvider, Provider, WikipediaProvider, provider_enabled
from .queries import Query, generate_queries, normalize_title
from .retrieval import RobotsCache, retrieve_full_text

log = logging.getLogger(__name__)

NOT_COVERED = [
    "Subscription-only publisher content (only abstracts or open-access versions can be compared).",
    "Private student-paper repositories such as Turnitin's database; they are not publicly accessible.",
    "Books and theses that are not openly available online.",
    "Web pages that block automated access, require login, or render text only with JavaScript.",
]


def default_http_client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(timeout=settings.http_timeout_s, headers={"User-Agent": settings.user_agent})


# Replaceable factory (tests inject an httpx.MockTransport-backed client).
http_client_factory = default_http_client


@dataclass
class DiscoveryResult:
    sources: list[SourceDoc]
    coverage: dict
    queries: list[dict]
    reference_checks: list[dict] = field(default_factory=list)


def _candidate_key(c: Candidate) -> str:
    if c.doi:
        return "doi:" + c.doi
    if c.url and c.kind == "web":
        u = re.sub(r"^https?://(www\.)?", "", c.url.lower()).rstrip("/")
        return "url:" + u.split("#")[0]
    return "title:" + normalize_title(c.title)[:200]


def _merge(a: Candidate, b: Candidate) -> Candidate:
    """Keep the richer record, union the evidence."""
    best = a if (len(a.text), bool(a.pdf_url)) >= (len(b.text), bool(b.pdf_url)) else b
    other = b if best is a else a
    best.query_ids = sorted(set(a.query_ids) | set(b.query_ids))
    best.pdf_url = best.pdf_url or other.pdf_url
    best.doi = best.doi or other.doi
    best.year = best.year or other.year
    best.authors = best.authors or other.authors
    best.provider_id = best.provider_id or other.provider_id
    if best.provider != other.provider and other.provider not in best.provider.split("+"):
        best.provider = f"{best.provider}+{other.provider}"
    return best


def _doc_shingles(text: str, k: int = 4) -> set:
    words = [t.word for t in tokenize(text)]
    return {tuple(words[i : i + k]) for i in range(len(words) - k + 1)}


def _snippet_overlap(c: Candidate, doc_shingles: set, k: int = 4) -> int:
    words = [t.word for t in tokenize(c.text)]
    return sum(1 for i in range(len(words) - k + 1) if tuple(words[i : i + k]) in doc_shingles)


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def discover_sources(
    text: str,
    paragraphs: list[Paragraph],
    sentences: list[Sentence],
    citations: CitationContext,
    lang: str,
    external_search: bool = True,
    progress=None,
    cancel_check=None,
    http_client: httpx.Client | None = None,
) -> DiscoveryResult:
    settings = get_settings()
    progress = progress or (lambda msg, frac=None: None)
    cancel_check = cancel_check or (lambda: None)

    queries = generate_queries(text, paragraphs, sentences, citations, lang)
    coverage_providers: list[dict] = []
    if not external_search:
        return DiscoveryResult(
            sources=[],
            coverage={
                "external_search": False,
                "providers": [
                    {"name": cls.name, "label": cls.label, "status": "disabled", "detail": "External search turned off in your privacy settings."}
                    for cls in PROVIDER_CLASSES
                ],
                "not_covered": NOT_COVERED,
                "summary": "External search was turned off, so only your library and collection documents were compared.",
            },
            queries=[q.__dict__ for q in queries],
        )

    owns_client = http_client is None
    shared_http = http_client or http_client_factory()
    providers: list[Provider] = []
    for cls in PROVIDER_CLASSES:
        client = ProviderClient(cls.name, shared_http)
        p = cls(settings, client)
        if not provider_enabled(settings, cls.name):
            coverage_providers.append({"name": cls.name, "label": cls.label, "status": "disabled", "detail": "Disabled by the administrator."})
            continue
        if not p.configured():
            coverage_providers.append(
                {"name": cls.name, "label": cls.label, "status": "not_configured", "detail": "No API key configured, so this source was not searched."}
            )
            continue
        providers.append(p)

    # Route queries; budget caps the number of provider calls.
    budget = settings.max_queries_per_document
    plan: list[tuple[Provider, Query]] = []
    # Interleave families so a small budget still covers each kind of query.
    by_family = {f: [q for q in queries if q.family == f] for f in ("reference", "phrase", "keyword")}
    rounds = max((len(v) for v in by_family.values()), default=0)
    for i in range(rounds):
        for fam in ("reference", "phrase", "keyword"):
            if i < len(by_family[fam]):
                q = by_family[fam][i]
                for p in providers:
                    if fam in p.families:
                        # Web search is the most valuable for phrases and has its own quota.
                        plan.append((p, q))
    per_provider_cap = {p.name: max(3, budget // max(1, len(providers)) * 2) for p in providers}
    used: dict[str, int] = {}
    capped_plan = []
    for p, q in plan:
        if used.get(p.name, 0) < per_provider_cap[p.name]:
            used[p.name] = used.get(p.name, 0) + 1
            capped_plan.append((p, q))
    capped_plan = capped_plan[: budget * 3]

    candidates: dict[str, Candidate] = {}
    per_provider_results: dict[str, int] = {p.name: 0 for p in providers}
    progress(f"Searching {len(providers)} sources with {len(queries)} queries", 0.0)

    def run(p: Provider, q: Query) -> tuple[Provider, Query, list[Candidate], str | None]:
        if not p.client.available:
            return p, q, [], "provider unavailable"
        try:
            return p, q, p.search(q), None
        except ProviderError as e:
            return p, q, [], str(e)
        except Exception as e:  # noqa: BLE001 - malformed provider responses must not kill the job
            log.exception("provider %s failed", p.name)
            return p, q, [], f"unexpected error: {type(e).__name__}"

    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run, p, q) for p, q in capped_plan]
        for fut in as_completed(futures):
            cancel_check()
            p, q, results, err = fut.result()
            done += 1
            progress(f"Searched {p.label.split(' (')[0]}: {len(results)} results", done / max(1, len(futures)))
            per_provider_results[p.name] += len(results)
            for c in results:
                c.query_ids = [q.id]
                key = _candidate_key(c)
                candidates[key] = _merge(candidates[key], c) if key in candidates else c

    # Rank candidates by evidence before spending effort on full text.
    family_of = {q.id: q.family for q in queries}
    doc_sh = _doc_shingles(text)
    scored = []
    for c in candidates.values():
        fams = [family_of.get(qid) for qid in c.query_ids]
        score = 3.0 * fams.count("phrase") + 1.5 * fams.count("reference") + 1.0 * fams.count("keyword")
        score += 0.5 * min(_snippet_overlap(c, doc_sh), 20)
        score += 1.0 / (1 + c.rank)
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    selected = [c for _, c in scored[: settings.max_sources_per_document]]

    progress(f"Retrieving text for {len(selected)} candidate sources", 0.0)
    robots = RobotsCache(shared_http, settings.user_agent)
    wikipedia = next((p for p in providers if isinstance(p, WikipediaProvider)), None)
    sources: list[SourceDoc] = []
    notes: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(retrieve_full_text, c, shared_http, robots, wikipedia): c for c in selected}
        for i, fut in enumerate(as_completed(futs)):
            cancel_check()
            c = futs[fut]
            try:
                body, text_kind, note = fut.result()
            except Exception as e:  # noqa: BLE001
                body, text_kind, note = c.text, c.text_kind, f"retrieval error: {type(e).__name__}"
            progress(f"Retrieved {i + 1} of {len(selected)} sources", (i + 1) / max(1, len(selected)))
            if not body or len(body.split()) < 12:
                continue
            sid = f"src{len(sources) + 1}"
            notes[sid] = note
            sources.append(
                SourceDoc(
                    id=sid,
                    title=c.title,
                    text=body,
                    kind=c.kind,
                    provider=c.provider,
                    text_kind=text_kind,
                    url=c.url,
                    doi=c.doi,
                    authors=c.authors,
                    year=c.year,
                    license=c.license,
                    found_by=c.query_ids,
                )
            )

    # Collapse near-duplicate texts (mirrors of the same article).
    duplicates_removed = 0
    if len(sources) > 1:
        sigs = [signature(shingle_set(s.text)) for s in sources]
        keep: list[SourceDoc] = []
        for group in near_duplicate_groups(sigs, threshold=0.8):
            members = [sources[i] for i in group]
            members.sort(key=lambda s: (s.text_kind != "full_text", s.doi is None, -len(s.text)))
            keep.append(members[0])
            duplicates_removed += len(members) - 1
        sources = sorted(keep, key=lambda s: int(s.id[3:]))

    # Reference verification against Crossref (DOIs) / OpenAlex (titles).
    reference_checks = verify_references(citations, providers)

    for p in providers:
        st = p.client.stats
        answered = st.successes + st.cache_hits
        if not st.requests and not st.cache_hits:
            status, detail = "not_used", "No queries were routed to this service within the query budget."
        elif answered == 0:
            status, detail = "unavailable", "; ".join(st.errors[:2]) or "No response"
        else:
            status, detail = ("degraded" if st.errors else "ok"), "; ".join(st.errors[:2])
        coverage_providers.append(
            {
                "name": p.name,
                "label": p.label,
                "status": status,
                "queries": used.get(p.name, 0),
                "requests": st.requests,
                "cache_hits": st.cache_hits,
                "results": per_provider_results.get(p.name, 0),
                "errors": st.errors[:3],
                "detail": detail,
            }
        )
    full = sum(1 for s in sources if s.text_kind == "full_text")
    if owns_client:
        shared_http.close()
    reachable = [c for c in coverage_providers if c["status"] in ("ok", "degraded")]
    n_queries = sum(c.get("queries", 0) for c in reachable)
    summary = (
        f"Searched {len(reachable)} external service{'s' if len(reachable) != 1 else ''} with {n_queries} queries; "
        f"compared against {len(sources)} source{'s' if len(sources) != 1 else ''} ({full} full text, {len(sources) - full} abstract/snippet only)."
    )
    if not reachable:
        summary = "No external search service could be reached, so no external sources were compared. This is a coverage gap, not a clean result."
    elif not sources:
        summary += " No retrievable source text was found, so external overlap could not be measured."
    return DiscoveryResult(
        sources=sources,
        coverage={
            "external_search": True,
            "providers": coverage_providers,
            "candidates_found": len(candidates),
            "sources_compared": len(sources),
            "full_text_sources": full,
            "duplicates_removed": duplicates_removed,
            "retrieval_notes": notes,
            "not_covered": NOT_COVERED,
            "summary": summary,
            "data_shared": "Only short excerpts (search phrases of about 10 words, keywords and reference titles) were sent to these services; the document itself was not.",
        },
        queries=[{"id": q.id, "family": q.family, "text": q.text, "start": q.origin_start, "end": q.origin_end} for q in queries],
        reference_checks=reference_checks,
    )


def verify_references(citations: CitationContext, providers: list[Provider], limit: int = 25) -> list[dict]:
    crossref = next((p for p in providers if isinstance(p, CrossrefProvider)), None)
    openalex = next((p for p in providers if isinstance(p, OpenAlexProvider)), None)
    checks = []
    for ref in citations.references[:limit]:
        result: dict = {"reference_index": ref.index, "status": "unchecked"}
        try:
            if ref.doi and crossref and crossref.client.available:
                v = crossref.verify_doi(ref.doi)
                result.update(v)
                if v["status"] == "verified" and ref.title_guess:
                    sim = _title_similarity(ref.title_guess, v.get("title", ""))
                    result["title_similarity"] = round(sim, 2)
                    if sim < 0.6:
                        result["status"] = "mismatch"
                        result["reason"] = "The DOI exists but belongs to a work with a different title."
            elif ref.title_guess and openalex and openalex.client.available:
                hits = openalex.search(Query("ref", "reference", ref.title_guess))
                best = max(hits, key=lambda c: _title_similarity(ref.title_guess, c.title), default=None)
                if best and _title_similarity(ref.title_guess, best.title) >= 0.85:
                    result.update({"status": "found", "title": best.title, "year": best.year, "doi": best.doi, "url": best.url})
                else:
                    result.update({"status": "not_found", "reason": "No scholarly record with this title was found (it may be a book, web page or unindexed work)."})
        except ProviderError as e:
            result.update({"status": "unchecked", "reason": str(e)})
        ref.verification = result
        checks.append(result)
    return checks
