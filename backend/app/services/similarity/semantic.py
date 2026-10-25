"""Semantic (paraphrase) similarity.

Pipeline per document:

1. Candidate document sentences: >= 8 words, outside the bibliography,
   not already explained by an exact match, not inside quotation marks.
2. Candidate source sentences from every retrieved source; when there are
   too many, the ones sharing the most content words with the document are
   kept (cheap lexical pre-filter before the expensive encoder).
3. Embed everything in one batch and retrieve, for each document sentence,
   the most similar source sentences (cosine on normalised vectors).
4. Reject pairs that are merely on the same topic: a pair must clear the
   provider's paraphrase threshold AND share a minimum fraction of content
   words (or clear the stricter "strong" threshold alone), and the two
   sentences must have comparable length.
5. Optionally rescore surviving pairs with a cross-encoder.
6. Consecutive document sentences that align, in order, with consecutive
   sentences of the same source form one passage; such sequences are much
   stronger evidence of a paraphrased passage than one similar sentence.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from ..text import Sentence, split_sentences, word_count
from .embeddings import EmbeddingProvider, Reranker, content_stems
from .types import Passage, SourceDoc

MAX_SOURCE_SENTENCES = 8000
TOP_K = 3
MIN_DOC_WORDS = 8
MIN_SRC_WORDS = 6
MIN_OVERLAP = 0.25  # overlap coefficient of content stems for a non-"strong" pair
RERANK_MIN = 0.72  # cross-encoder (STS-B scale 0-1) score needed to keep a pair


@dataclass
class _SrcSent:
    source_idx: int
    index: int  # sentence position within the source
    start: int
    end: int
    text: str
    stems: frozenset


def _overlap(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def find_semantic_passages(
    sentences: list[Sentence],
    eligible: list[bool],
    sources: list[SourceDoc],
    provider: EmbeddingProvider,
    reranker: Reranker | None = None,
) -> tuple[list[Passage], dict]:
    stats = {"doc_sentences": 0, "source_sentences": 0, "pairs_scored": 0, "pairs_kept": 0, "reranked": reranker is not None}
    doc_sents = [s for s, ok in zip(sentences, eligible, strict=True) if ok and word_count(s.text) >= MIN_DOC_WORDS]
    stats["doc_sentences"] = len(doc_sents)
    if not doc_sents or not sources:
        return [], stats

    doc_stems = [frozenset(content_stems(s.text)) for s in doc_sents]
    doc_vocab = Counter(st for stems in doc_stems for st in stems)

    src_sents: list[_SrcSent] = []
    for si, src in enumerate(sources):
        for idx, (a, b) in enumerate(split_sentences(src.text)):
            text = src.text[a:b]
            if word_count(text) < MIN_SRC_WORDS or len(text) > 1500:
                continue
            src_sents.append(_SrcSent(si, idx, a, b, text, frozenset(content_stems(text))))
    if not src_sents:
        return [], stats
    if len(src_sents) > MAX_SOURCE_SENTENCES:
        src_sents.sort(key=lambda s: -sum(1 for st in s.stems if st in doc_vocab))
        src_sents = src_sents[:MAX_SOURCE_SENTENCES]
    stats["source_sentences"] = len(src_sents)

    vectors = provider.encode([s.text for s in doc_sents] + [s.text for s in src_sents])
    dv, sv = vectors[: len(doc_sents)], vectors[len(doc_sents) :]
    sims = dv @ sv.T  # cosine similarity (rows are normalised)

    candidates: list[tuple[int, int, float, float]] = []  # doc idx, src idx, cosine, overlap
    k = min(TOP_K, sims.shape[1])
    top = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]
    for i in range(len(doc_sents)):
        for j in top[i]:
            cos = float(sims[i, j])
            stats["pairs_scored"] += 1
            if cos < provider.paraphrase_threshold:
                continue
            ov = _overlap(doc_stems[i], src_sents[j].stems)
            if cos < provider.strong_threshold and ov < MIN_OVERLAP:
                continue  # similar meaning-space but little shared content: topical, not paraphrase
            ld, ls = word_count(doc_sents[i].text), word_count(src_sents[j].text)
            if not (0.4 <= ld / ls <= 2.5):
                continue
            candidates.append((i, j, cos, ov))

    rerank_scores: dict[tuple[int, int], float] = {}
    if reranker and candidates:
        scores = reranker.score([(doc_sents[i].text, src_sents[j].text) for i, j, _, _ in candidates])
        rerank_scores = {(i, j): s for (i, j, _, _), s in zip(candidates, scores, strict=True)}
        candidates = [c for c in candidates if rerank_scores[(c[0], c[1])] >= RERANK_MIN]

    # Best source sentence per document sentence, per source.
    best: dict[tuple[int, int], tuple[int, float, float]] = {}
    for i, j, cos, ov in candidates:
        key = (i, src_sents[j].source_idx)
        if key not in best or cos > best[key][1]:
            best[key] = (j, cos, ov)
    stats["pairs_kept"] = len(best)

    # Group consecutive document sentences aligned in order with the same source.
    by_source: dict[int, list[tuple[int, int, float, float]]] = defaultdict(list)
    for (i, si), (j, cos, ov) in best.items():
        by_source[si].append((i, j, cos, ov))

    passages: list[Passage] = []
    for si, items in by_source.items():
        items.sort()
        groups: list[list[tuple[int, int, float, float]]] = []
        for it in items:
            if groups:
                pi, pj, _, _ = groups[-1][-1]
                same_para = doc_sents[it[0]].paragraph == doc_sents[pi].paragraph
                if it[0] == pi + 1 and same_para and 0 < src_sents[it[1]].index - src_sents[pj].index <= 3:
                    groups[-1].append(it)
                    continue
            groups.append([it])
        src = sources[si]
        for g in groups:
            first, last = doc_sents[g[0][0]], doc_sents[g[-1][0]]
            cos_mean = float(np.mean([x[2] for x in g]))
            cos_max = max(x[2] for x in g)
            strong = len(g) >= 2 or cos_max >= provider.strong_threshold
            s_first = min(src_sents[x[1]].start for x in g)
            s_last = max(src_sents[x[1]].end for x in g)
            words = sum(word_count(doc_sents[x[0]].text) for x in g)
            pairs = [
                {
                    "sentence": doc_sents[x[0]].text,
                    "source_sentence": src_sents[x[1]].text,
                    "cosine": round(x[2], 3),
                    "shared_content_words": round(x[3], 3),
                    **({"cross_encoder": round(rerank_scores[(x[0], x[1])], 3)} if rerank_scores else {}),
                }
                for x in g
            ]
            passages.append(
                Passage(
                    source_id=src.id,
                    start=first.start,
                    end=last.end,
                    words=words,
                    matched_words=words,
                    method="semantic",
                    score=round(cos_mean, 3),
                    source_excerpt=src.text[s_first:s_last][:4000],
                    source_start=s_first,
                    source_end=s_last,
                    evidence={
                        "provider": provider.name,
                        "aligned_sentences": len(g),
                        "mean_cosine": round(cos_mean, 3),
                        "max_cosine": round(cos_max, 3),
                        "strength": "strong" if strong else "moderate",
                        "pairs": pairs,
                    },
                )
            )
    return passages, stats
