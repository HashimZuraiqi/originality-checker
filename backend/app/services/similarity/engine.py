"""Combine exact and semantic matching into classified, explained findings.

Scoring is deliberately simple so every number can be recomputed by hand:

* similarity_index = words in exact/near-exact matches that still need
  attention (unattributed, cited-but-not-quoted, quoted-but-uncited)
  divided by the countable words (all words minus the reference list).
  Correctly quoted *and* cited text, and reference-list overlap, are shown
  but not counted.
* unattributed_pct = the subset with no quotation marks and no citation.
* semantic_pct = words in sentences flagged as possible paraphrase. It is
  reported separately because semantic similarity is probabilistic
  evidence, not proof of copying.
* quoted_pct = words in quoted-and-cited matches (informational).

Each document word is attributed to at most one source (the source with the
largest overall overlap claims shared words first), so per-source
percentages add up to the overall figure.
"""

from collections import defaultdict

from ..citations import CitationContext
from ..text import Sentence, Token, tokenize
from .embeddings import EmbeddingProvider, Reranker
from .exact import build_doc_index, find_exact_passages
from .semantic import find_semantic_passages
from .types import Passage, SourceDoc

DEFAULT_OPTIONS = {
    "shingle_size": 5,
    "min_match_words": 8,
    "exclude_quotes": True,  # don't count quoted+cited text in the index
    "exclude_bibliography": True,
    "semantic": True,
}

CATEGORY_INFO = {
    "unattributed": ("high", "Possible unattributed copying"),
    "cited_no_quotes": ("medium", "Copied wording with a citation but no quotation marks"),
    "quoted_uncited": ("medium", "Quotation without a citation"),
    "quoted_cited": ("info", "Correctly quoted and cited"),
    "bibliography": ("info", "Reference-list overlap"),
    "paraphrase": ("medium", "Possible paraphrase without citation"),
    "paraphrase_cited": ("low", "Paraphrase with a citation nearby"),
}


def _token_index_at(tokens: list[Token], char: int) -> int:
    lo, hi = 0, len(tokens)
    while lo < hi:
        mid = (lo + hi) // 2
        if tokens[mid].end <= char:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _attribute(passages: list[Passage], tokens: list[Token], sources: list[SourceDoc], min_words: int) -> list[Passage]:
    """Assign each document token to one source and trim passages."""
    token_sets: dict[str, set[int]] = defaultdict(set)
    spans: dict[int, tuple[int, int]] = {}
    for idx, p in enumerate(passages):
        a = _token_index_at(tokens, p.start)
        b = _token_index_at(tokens, p.end - 1)
        spans[idx] = (a, b)
        token_sets[p.source_id].update(range(a, b + 1))
    order = sorted(token_sets, key=lambda sid: (-len(token_sets[sid]), sid))
    owner: dict[int, str] = {}
    for sid in order:
        for t in token_sets[sid]:
            owner.setdefault(t, sid)

    rank = {sid: i for i, sid in enumerate(order)}
    result: list[Passage] = []
    for idx in sorted(spans, key=lambda i: (rank[passages[i].source_id], passages[i].start)):
        p = passages[idx]
        a, b = spans[idx]
        run_start = None
        for t in range(a, b + 2):
            mine = t <= b and owner.get(t) == p.source_id
            if mine and run_start is None:
                run_start = t
            elif not mine and run_start is not None:
                length = t - run_start
                if length >= min_words:
                    trimmed = length < (b - a + 1)
                    result.append(
                        Passage(
                            source_id=p.source_id,
                            start=tokens[run_start].start,
                            end=tokens[t - 1].end,
                            words=length,
                            matched_words=min(p.matched_words, length),
                            method=p.method,
                            score=p.score,
                            source_excerpt=p.source_excerpt,
                            source_start=p.source_start,
                            source_end=p.source_end,
                            evidence={**p.evidence, **({"trimmed_overlap_with_other_source": True} if trimmed else {})},
                        )
                    )
                else:
                    for tt in range(run_start, t):
                        if owner.get(tt) == p.source_id:
                            owner[tt] = "__released__"
                run_start = None
    result.sort(key=lambda p: p.start)
    return result


def _explain(category: str, p: Passage, source: SourceDoc, provider_label: str | None) -> tuple[str, str]:
    title = f"“{source.title}”"
    limited = " (only the source's abstract/snippet was available)" if source.text_kind != "full_text" else ""
    if p.method in ("exact", "near_exact"):
        how = (
            f"{p.words} consecutive words match {title}{limited} "
            f"{'word for word' if p.method == 'exact' else 'with minor changes'}: "
            f"{round(p.score * 100)}% of the words are covered by identical {p.evidence.get('shingle_size', 5)}-word sequences."
        )
    else:
        ev = p.evidence
        how = (
            f"{ev['aligned_sentences']} sentence(s) closely resemble sentences in {title}{limited} "
            f"(mean semantic similarity {ev['mean_cosine']:.2f} with {provider_label}; "
            f"{'several consecutive sentences follow the source in order' if ev['aligned_sentences'] > 1 else 'single-sentence match'})."
        )
    steps = {
        "unattributed": "If this wording comes from the source, put it in quotation marks with a citation, "
        "or rewrite it in your own words and cite the source. If it is your own original text, no action is needed.",
        "cited_no_quotes": "The source is cited, but the wording is copied. Add quotation marks around the copied words "
        "or paraphrase properly while keeping the citation.",
        "quoted_uncited": "Add a citation naming the source of this quotation.",
        "quoted_cited": "No action needed: the quotation is marked and cited. Check that the quote is necessary and accurate.",
        "bibliography": "No action needed: reference lists naturally match the publications they cite.",
        "paraphrase": "Compare both passages. If your text restates this source, cite it. "
        "Shared topic vocabulary alone is not plagiarism, so review in context before drawing conclusions.",
        "paraphrase_cited": "A citation was found nearby, which is how paraphrased material should be credited. "
        "Check that the citation refers to this source and the wording is sufficiently your own.",
    }[category]
    return how, steps


def analyze_similarity(
    text: str,
    sentences: list[Sentence],
    citations: CitationContext,
    sources: list[SourceDoc],
    provider: EmbeddingProvider | None,
    reranker: Reranker | None = None,
    options: dict | None = None,
    cancel_check=None,
) -> dict:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    k = int(opts["shingle_size"])
    min_words = max(k, int(opts["min_match_words"]))
    tokens = tokenize(text)
    total_words = len(tokens)
    bib_words = (
        sum(1 for t in tokens if citations.in_bibliography(t.start)) if citations.bibliography_start is not None else 0
    )
    countable = max(1, total_words - (bib_words if opts["exclude_bibliography"] else 0))

    excluded = [False] * len(tokens)  # nothing excluded from *detection*; classification decides counting
    doc_index = build_doc_index(tokens, k, excluded)
    raw: list[Passage] = []
    for src in sources:
        if cancel_check:
            cancel_check()
        raw.extend(find_exact_passages(text, tokens, doc_index, src, k=k, min_words=min_words))
    exact = _attribute(raw, tokens, sources, min_words)

    # Sentences mostly explained by an exact match are not re-checked semantically.
    covered = [False] * len(text)
    for p in exact:
        for c in range(p.start, p.end):
            covered[c] = True
    eligible = []
    for s in sentences:
        span = max(1, s.end - s.start)
        cov = sum(covered[s.start : s.end]) / span
        eligible.append(
            cov < 0.5 and not citations.in_bibliography(s.start) and citations.quoted_fraction(s.start, s.end) < 0.5
        )

    semantic: list[Passage] = []
    sem_stats: dict = {}
    if opts["semantic"] and provider is not None:
        if cancel_check:
            cancel_check()
        semantic, sem_stats = find_semantic_passages(sentences, eligible, sources, provider, reranker)
        # One source per sentence region: keep the strongest.
        semantic.sort(key=lambda p: (-p.evidence["aligned_sentences"], -p.score))
        taken: list[tuple[int, int]] = []
        kept = []
        for p in semantic:
            if all(p.end <= a or p.start >= b for a, b in taken):
                kept.append(p)
                taken.append((p.start, p.end))
        semantic = sorted(kept, key=lambda p: p.start)

    by_id = {s.id: s for s in sources}
    findings: list[dict] = []
    counted_words = 0
    unattributed_words = 0
    quoted_words = 0
    semantic_words = 0
    per_source: dict[str, dict] = defaultdict(lambda: {"words": 0, "semantic_words": 0, "matches": 0, "categories": defaultdict(int)})
    provider_label = provider.label if provider else None

    for p in exact + semantic:
        src = by_id[p.source_id]
        cited = citations.has_citation_near(p.start, p.end, sentences)
        if p.method == "semantic":
            category = "paraphrase_cited" if cited else "paraphrase"
            semantic_words += p.words
            per_source[p.source_id]["semantic_words"] += p.words
        elif citations.in_bibliography(p.start):
            category = "bibliography"
        elif citations.quoted_fraction(p.start, p.end) >= 0.8:
            category = "quoted_cited" if cited else "quoted_uncited"
        else:
            category = "cited_no_quotes" if cited else "unattributed"

        counts = False
        if p.method != "semantic":
            counts = category in ("unattributed", "cited_no_quotes", "quoted_uncited") or (
                category == "quoted_cited" and not opts["exclude_quotes"]
            ) or (category == "bibliography" and not opts["exclude_bibliography"])
            if counts:
                counted_words += p.words
                per_source[p.source_id]["words"] += p.words
            if category == "unattributed":
                unattributed_words += p.words
            if category == "quoted_cited":
                quoted_words += p.words
        severity, label = CATEGORY_INFO[category]
        if category == "unattributed" and p.words < 15:
            severity = "medium"
        if category == "paraphrase" and p.evidence.get("strength") != "strong":
            severity = "low"
        how, steps = _explain(category, p, src, provider_label)
        per_source[p.source_id]["matches"] += 1
        per_source[p.source_id]["categories"][category] += 1
        findings.append(
            {
                "kind": "similarity",
                "category": category,
                "label": label,
                "severity": severity,
                "start": p.start,
                "end": p.end,
                "words": p.words,
                "method": p.method,
                "score": p.score,
                "source_id": p.source_id,
                "source_excerpt": p.source_excerpt,
                "citation_detected": cited,
                "counts_toward_index": counts,
                "explanation": how,
                "next_steps": steps,
                "evidence": p.evidence,
            }
        )
    findings.sort(key=lambda f: (f["start"], f["end"]))
    for i, f in enumerate(findings):
        f["id"] = f"sim-{i + 1}"

    def pct(words: int) -> float:
        return round(100 * words / countable, 1)

    source_rows = []
    for sid, row in per_source.items():
        src = by_id[sid]
        source_rows.append(
            {
                **src.public(),
                "words": row["words"],
                "percent": pct(row["words"]),
                "semantic_words": row["semantic_words"],
                "semantic_percent": pct(row["semantic_words"]),
                "matches": row["matches"],
                "categories": dict(row["categories"]),
            }
        )
    source_rows.sort(key=lambda r: (-r["words"], -r["semantic_words"], r["title"]))
    for i, r in enumerate(source_rows):
        r["rank"] = i + 1

    return {
        "scores": {
            "similarity_index": pct(counted_words),
            "unattributed_pct": pct(unattributed_words),
            "semantic_pct": pct(semantic_words),
            "quoted_pct": pct(quoted_words),
            "total_words": total_words,
            "countable_words": countable,
            "bibliography_words": bib_words,
            "matched_words": counted_words,
        },
        "findings": findings,
        "sources": source_rows,
        "method": {
            "shingle_size": k,
            "min_match_words": min_words,
            "exclude_quotes": opts["exclude_quotes"],
            "exclude_bibliography": opts["exclude_bibliography"],
            "semantic_provider": provider.describe() if provider else None,
            "reranker": getattr(reranker, "model_name", None),
            "semantic_stats": sem_stats,
            "formula": "similarity_index = words in counted exact/near-exact matches ÷ countable words "
            "(total words minus reference list). Semantic matches are reported separately.",
        },
    }
