"""Exact and near-exact overlap detection.

Method (word-shingle fingerprinting, as in Broder 1997 / Schleimer et al.
2003, without winnowing because the document side is small):

1. Tokenise and normalise (case, punctuation, Unicode forms).
2. Hash every k-word shingle of the *document* into an index.
3. Stream each source's shingles against that index (memory is bounded by
   the document, not by the sources).
4. Chain shingle hits whose document and source positions advance together;
   small gaps are allowed, so a passage with a few substituted words is
   found as one "near-exact" match instead of several fragments.
5. Drop chains shorter than `min_words` or with too few content words:
   short or stop-word-only overlaps are common phrasing, not copying.
"""

from collections import defaultdict
from dataclasses import dataclass

from ..text import STOPWORDS, Token, tokenize
from .types import Passage, SourceDoc

MAX_HITS_PER_SHINGLE = 8  # a shingle occurring more often is boilerplate


@dataclass
class _Chain:
    doc_first: int
    doc_last: int
    src_first: int
    src_last: int
    covered: set


def _shingle_hashes(tokens: list[Token], k: int) -> list[int]:
    words = [t.word for t in tokens]
    return [hash(tuple(words[i : i + k])) for i in range(len(words) - k + 1)]


def build_doc_index(doc_tokens: list[Token], k: int, excluded: list[bool]) -> dict[int, list[int]]:
    index: dict[int, list[int]] = defaultdict(list)
    for i, h in enumerate(_shingle_hashes(doc_tokens, k)):
        if any(excluded[i : i + k]):
            continue
        index[h].append(i)
    return index


def find_exact_passages(
    doc_text: str,
    doc_tokens: list[Token],
    doc_index: dict[int, list[int]],
    source: SourceDoc,
    k: int = 5,
    min_words: int = 8,
    max_gap: int = 4,
) -> list[Passage]:
    src_tokens = tokenize(source.text)
    if len(src_tokens) < k or not doc_index:
        return []
    hits: list[tuple[int, int]] = []
    for j, h in enumerate(_shingle_hashes(src_tokens, k)):
        positions = doc_index.get(h)
        if positions and len(positions) <= MAX_HITS_PER_SHINGLE:
            for p in positions:
                hits.append((p, j))
    if not hits:
        return []
    hits.sort()

    # Greedy chaining along the document. A hit extends a chain when both the
    # document and source positions move forward by a similar, small step.
    chains: list[_Chain] = []
    open_chains: list[_Chain] = []
    for p, j in hits:
        best = None
        for c in open_chains:
            dp, dj = p - c.doc_last, j - c.src_last
            if 0 < dp <= max_gap + 1 and 0 < dj <= max_gap + 1 and abs(dp - dj) <= max_gap:
                if best is None or dp < p - best.doc_last:
                    best = c
        if best is not None:
            best.doc_last, best.src_last = p, j
            best.covered.update(range(p, p + k))
        else:
            c = _Chain(p, p, j, j, set(range(p, p + k)))
            chains.append(c)
            open_chains.append(c)
        open_chains = [c for c in open_chains if p - c.doc_last <= max_gap + 1]

    passages: list[Passage] = []
    for c in chains:
        d0, d1 = c.doc_first, c.doc_last + k - 1  # inclusive token range
        s0, s1 = c.src_first, c.src_last + k - 1
        span_words = d1 - d0 + 1
        if span_words < min_words:
            continue
        content = sum(1 for t in doc_tokens[d0 : d1 + 1] if t.word not in STOPWORDS and not t.word.isdigit())
        if content < max(4, span_words // 4):
            continue
        matched = len(c.covered)
        ratio = matched / span_words
        start, end = doc_tokens[d0].start, doc_tokens[d1].end
        src_start, src_end = src_tokens[s0].start, src_tokens[min(s1, len(src_tokens) - 1)].end
        passages.append(
            Passage(
                source_id=source.id,
                start=start,
                end=end,
                words=span_words,
                matched_words=matched,
                method="exact" if ratio >= 0.97 else "near_exact",
                score=round(ratio, 3),
                source_excerpt=source.text[src_start:src_end][:4000],
                source_start=src_start,
                source_end=src_end,
                evidence={"shingle_size": k, "covered_words": matched, "coverage": round(ratio, 3)},
            )
        )
    return passages
