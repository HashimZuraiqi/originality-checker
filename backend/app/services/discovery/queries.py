"""Search-query generation from a document.

Three query families, each suited to different providers:

* phrase   - exact 8-12 word windows taken from the most *distinctive*
             sentences (rare vocabulary, numbers, named entities). Copied
             text is found best by exact-phrase search (web, Wikipedia).
* keyword  - weighted keywords and named entities for the whole document
             and for each long paragraph. Academic APIs rank by topical
             relevance, which surfaces sources of paraphrased material.
* reference - titles from the document's own reference list. The works a
             student cites are the most likely sources of copied text.

Only these short excerpts ever leave the server; the full document is never
sent to a search provider.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..citations import CitationContext
from ..text import STOPWORDS, Paragraph, Sentence, normalize_word, tokenize, word_count

try:
    from wordfreq import zipf_frequency
except ImportError:  # pragma: no cover
    def zipf_frequency(word: str, lang: str) -> float:
        return 4.0


@dataclass
class Query:
    id: str
    family: str  # phrase | keyword | reference
    text: str
    origin_start: int | None = None  # document offset that produced it
    origin_end: int | None = None
    weight: float = 1.0
    meta: dict = field(default_factory=dict)


_ENTITY_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]+(?:\s+(?:of|de|la|the|and)?\s*[A-Z][a-z]+)+|[A-Z]{2,6}\b)")


def _rarity(word: str, lang: str) -> float:
    """0 for very common words, up to ~7 for words absent from the corpus."""
    return max(0.0, 7.0 - zipf_frequency(word, lang))


def named_entities(text: str) -> list[str]:
    found = Counter()
    for m in _ENTITY_RE.finditer(text):
        ent = m.group(1).strip()
        if ent.lower() in STOPWORDS or len(ent) < 3:
            continue
        found[ent] += 1
    return [e for e, _ in found.most_common(20)]


def keywords(text: str, lang: str, n: int = 8) -> list[str]:
    counts = Counter(t.word for t in tokenize(text) if t.word not in STOPWORDS and len(t.word) > 3 and not t.word.isdigit())
    scored = {w: (1 + math.log(c)) * _rarity(w, lang) for w, c in counts.items()}
    return [w for w, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:n]]


def _sentence_distinctiveness(s: Sentence, lang: str) -> float:
    toks = tokenize(s.text)
    n = len(toks)
    if n < 10 or n > 60:
        return 0.0
    content = [t.word for t in toks if t.word not in STOPWORDS]
    if len(content) < 5:
        return 0.0
    rarity = sum(_rarity(w, lang) for w in content) / len(content)
    has_number = any(ch.isdigit() for ch in s.text)
    caps = sum(1 for t in toks[1:] if s.text[t.start - s.start : t.start - s.start + 1].isupper())
    return rarity + 0.6 * has_number + 0.3 * min(caps, 4)


def _best_window(s: Sentence, lang: str, size: int = 10) -> tuple[str, int, int]:
    toks = tokenize(s.text)
    if len(toks) <= size:
        return s.text.strip(), s.start, s.end
    best, best_i = -1.0, 0
    for i in range(len(toks) - size + 1):
        score = sum(_rarity(t.word, lang) for t in toks[i : i + size] if t.word not in STOPWORDS)
        if score > best:
            best, best_i = score, i
    a, b = toks[best_i].start, toks[best_i + size - 1].end
    return s.text[a:b], s.start + a, s.start + b


def generate_queries(
    text: str,
    paragraphs: list[Paragraph],
    sentences: list[Sentence],
    citations: CitationContext,
    lang: str = "en",
    max_phrase: int = 10,
    max_keyword: int = 6,
    max_reference: int = 8,
) -> list[Query]:
    lang = lang if lang in ("en", "de", "fr", "es", "it", "pt", "nl") else "en"
    body_sents = [s for s in sentences if not citations.in_bibliography(s.start)]
    queries: list[Query] = []

    # Phrase queries: most distinctive sentences, spread across the document
    # (best sentence per equal-size slice) so coverage is not front-loaded.
    scored = [(s, _sentence_distinctiveness(s, lang)) for s in body_sents]
    scored = [x for x in scored if x[1] > 0]
    if scored:
        slices = max(1, min(max_phrase, len(scored)))
        per = math.ceil(len(scored) / slices)
        picked = []
        for k in range(0, len(scored), per):
            chunk = scored[k : k + per]
            picked.append(max(chunk, key=lambda x: x[1]))
        picked = sorted(picked, key=lambda x: -x[1])[:max_phrase]
        for s, score in sorted(picked, key=lambda x: x[0].start):
            phrase, a, b = _best_window(s, lang)
            phrase = re.sub(r"[\"“”]", "", phrase).strip()
            queries.append(Query(f"p{len(queries) + 1}", "phrase", phrase, a, b, weight=score))

    # Keyword queries: whole document, then the longest paragraphs.
    body_text = text[: citations.bibliography_start] if citations.bibliography_start else text
    doc_kw = keywords(body_text, lang, 6)
    ents = named_entities(body_text)
    if doc_kw:
        main = " ".join((ents[:2] + doc_kw)[:6])
        queries.append(Query(f"k{len(queries) + 1}", "keyword", main, None, None, weight=2.0, meta={"scope": "document"}))
    long_paras = sorted(
        (p for p in paragraphs if word_count(p.text) >= 60 and not citations.in_bibliography(p.start)),
        key=lambda p: -word_count(p.text),
    )
    seen = {queries[-1].text} if doc_kw else set()
    for p in long_paras:
        if sum(1 for q in queries if q.family == "keyword") >= max_keyword:
            break
        kw = keywords(p.text, lang, 5)
        if len(kw) < 3:
            continue
        q = " ".join(kw)
        if q in seen:
            continue
        seen.add(q)
        queries.append(Query(f"k{len(queries) + 1}", "keyword", q, p.start, p.end, weight=1.0, meta={"scope": "paragraph"}))

    # Reference titles.
    for ref in citations.references[:max_reference]:
        if ref.title_guess and word_count(ref.title_guess) >= 3:
            queries.append(
                Query(
                    f"r{len(queries) + 1}",
                    "reference",
                    ref.title_guess[:250],
                    ref.start,
                    ref.end,
                    weight=1.5,
                    meta={"doi": ref.doi, "reference_index": ref.index, "first_author": ref.first_author, "year": ref.year},
                )
            )
    return queries


def normalize_title(title: str) -> str:
    return " ".join(normalize_word(w) for w in re.findall(r"[^\W_]+", title))
