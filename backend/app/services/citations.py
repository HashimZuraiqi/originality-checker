"""Citation and reference analysis.

Finds in-text citations (APA/Harvard author-year, narrative, IEEE numeric,
MLA author-page), quotations and the reference list, links citations to
reference entries, and reports problems. The similarity engine uses
`CitationContext` to decide whether a matched passage was attributed.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .text import Sentence

BIB_HEADING_RE = re.compile(
    r"^[ \t]*(?:\d+\.?\s*)?(references|reference list|bibliography|works cited|literature cited|sources|citations)"
    r"[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_SURNAME = r"(?:van |von |de |da |del |la |le |di |du )?[A-Z][A-Za-z'’\-]+"
_YEAR = r"(?:1[6-9]\d{2}|20\d{2})[a-z]?|n\.d\."
# One author-year item inside parentheses, e.g. "Smith & Jones, 2019, p. 4".
_AY_ITEM = (
    rf"(?:see |e\.g\.,? |cf\. )?(?P<authors>{_SURNAME}(?: et al\.)?(?:(?:,? (?:&|and) |, ){_SURNAME})*)"
    rf",? (?P<year>{_YEAR})(?:, (?:pp?\.|para\.|ch\.) ?[\dA-Za-z]+(?:[-–]\d+)?)?"
)
PAREN_RE = re.compile(rf"\((?:{_AY_ITEM.replace('?P<authors>', '').replace('?P<year>', '')})(?:; ?(?:{_AY_ITEM.replace('?P<authors>', '').replace('?P<year>', '')}))*\)")
AY_ITEM_RE = re.compile(_AY_ITEM)
NARRATIVE_RE = re.compile(
    rf"(?P<authors>{_SURNAME}(?: et al\.)?(?:(?:,? (?:&|and) ){_SURNAME})?) \((?P<year>{_YEAR})(?:, p\. ?\d+)?\)"
)
NUMERIC_RE = re.compile(r"\[(?P<nums>\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
MLA_RE = re.compile(rf"\((?P<authors>{_SURNAME}(?: et al\.)?) (?P<page>\d{{1,4}}(?:[-–]\d{{1,4}})?)\)")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"]+")
QUOTE_PAIRS = [('"', '"'), ("“", "”"), ("«", "»"), ("„", "“")]


@dataclass
class InTextCitation:
    start: int
    end: int
    text: str
    style: str  # author-year | narrative | numeric | mla
    authors: list[str] = field(default_factory=list)
    year: str | None = None
    numbers: list[int] = field(default_factory=list)
    # (authors, year) per item; "(A, 2020; B, 2019)" has two items.
    items: list[tuple[list[str], str | None]] = field(default_factory=list)
    reference_index: int | None = None


@dataclass
class ReferenceEntry:
    index: int
    start: int
    end: int
    text: str
    number: int | None
    first_author: str | None
    year: str | None
    doi: str | None
    url: str | None
    title_guess: str | None
    cited: bool = False
    verification: dict | None = None  # filled by the pipeline (Crossref)


@dataclass
class CitationContext:
    citations: list[InTextCitation]
    references: list[ReferenceEntry]
    quotes: list[tuple[int, int]]
    bibliography_start: int | None
    issues: list[dict]
    style: str | None
    _cite_starts: list[int] = field(default_factory=list, repr=False)

    def has_citation_near(self, start: int, end: int, sentences: list[Sentence]) -> bool:
        """True if an in-text citation sits in the sentence(s) spanning
        [start, end) or in the sentence right after (attribution often
        follows the quoted/paraphrased material)."""
        if not self.citations:
            return False
        lo, hi = start, end
        for i, s in enumerate(sentences):
            if s.end > start and s.start < end:
                lo = min(lo, s.start)
                hi = max(hi, s.end)
                if i + 1 < len(sentences) and sentences[i + 1].paragraph == s.paragraph:
                    hi = max(hi, sentences[i + 1].end)
        idx = bisect_right(self._cite_starts, hi) - 1
        return idx >= 0 and self.citations[idx].start >= lo

    def quoted_fraction(self, start: int, end: int) -> float:
        if end <= start:
            return 0.0
        covered = 0
        for qs, qe in self.quotes:
            ov = min(end, qe) - max(start, qs)
            if ov > 0:
                covered += ov
        return min(1.0, covered / (end - start))

    def in_bibliography(self, start: int) -> bool:
        return self.bibliography_start is not None and start >= self.bibliography_start


def find_bibliography_start(text: str) -> int | None:
    """Offset of a reference-list heading in the last 70% of the document."""
    found = None
    for m in BIB_HEADING_RE.finditer(text):
        if m.start() >= len(text) * 0.3:
            found = m.start()  # keep the last one (appendix "Sources" etc. are rarer than body ones)
    return found


def find_quotes(text: str, limit: int | None = None) -> list[tuple[int, int]]:
    """Quoted spans (including the marks). Very long "quotes" are almost
    always unbalanced marks, so spans over ~250 words are ignored."""
    spans: list[tuple[int, int]] = []
    end_limit = len(text) if limit is None else limit
    for open_q, close_q in QUOTE_PAIRS:
        i = 0
        while i < end_limit:
            s = text.find(open_q, i, end_limit)
            if s == -1:
                break
            e = text.find(close_q, s + 1, end_limit)
            if e == -1:
                break
            if text.count(" ", s, e) <= 250 and "\n\n" not in text[s:e]:
                spans.append((s, e + 1))
                i = e + 1
            else:
                i = s + 1
    spans.sort()
    return spans


def _surname_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower().split()[-1]) if name.strip() else ""


def _split_authors(authors: str) -> list[str]:
    authors = authors.replace(" et al.", "")
    parts = re.split(r",? (?:&|and) |, ", authors)
    return [p.strip() for p in parts if p.strip()]


def _expand_numbers(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", spec):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a <= 50:
                out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def extract_citations(text: str, end_limit: int) -> list[InTextCitation]:
    body = text[:end_limit]
    found: list[InTextCitation] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= ts or s >= te for ts, te in taken)

    for m in PAREN_RE.finditer(body):
        items = [(_split_authors(it.group("authors")), it.group("year")) for it in AY_ITEM_RE.finditer(m.group())]
        authors = [a for its, _ in items for a in its]
        year = items[0][1] if items else None
        found.append(InTextCitation(m.start(), m.end(), m.group(), "author-year", authors, year, items=items))
        taken.append((m.start(), m.end()))
    for m in NARRATIVE_RE.finditer(body):
        if free(m.start(), m.end()):
            found.append(
                InTextCitation(m.start(), m.end(), m.group(), "narrative", _split_authors(m.group("authors")), m.group("year"))
            )
            taken.append((m.start(), m.end()))
    for m in NUMERIC_RE.finditer(body):
        if free(m.start(), m.end()):
            nums = _expand_numbers(m.group("nums"))
            if nums:
                found.append(InTextCitation(m.start(), m.end(), m.group(), "numeric", numbers=nums))
                taken.append((m.start(), m.end()))
    for m in MLA_RE.finditer(body):
        if free(m.start(), m.end()):
            found.append(InTextCitation(m.start(), m.end(), m.group(), "mla", [m.group("authors")]))
            taken.append((m.start(), m.end()))
    found.sort(key=lambda c: c.start)
    return found


def parse_references(text: str, start: int) -> list[ReferenceEntry]:
    heading = BIB_HEADING_RE.search(text, start)
    body_start = heading.end() if heading else start
    entries: list[ReferenceEntry] = []
    # Entries are separated by newlines (blank or single); numbered lists may
    # also be glued together on one line after PDF extraction.
    chunks: list[tuple[int, int]] = []
    for m in re.finditer(r"[^\n]+", text[body_start:]):
        line = m.group().strip()
        if not line:
            continue
        s = body_start + m.start()
        e = body_start + m.end()
        # A line that does not look like the start of a new entry continues the previous one.
        starts_entry = bool(
            re.match(r"^\s*(\[\d+\]|\d+\.\s|[A-Z][A-Za-z'’\-]+,\s|[A-Z][a-z]+ [A-Z]\.|[A-Z]{2,})", line)
        )
        if chunks and not starts_entry:
            chunks[-1] = (chunks[-1][0], e)
        else:
            chunks.append((s, e))
    for s, e in chunks:
        raw = text[s:e].strip()
        if len(raw) < 15:
            continue
        num_m = re.match(r"^\s*\[?(\d{1,3})[\].]\s*", raw)
        number = int(num_m.group(1)) if num_m else None
        rest = raw[num_m.end():] if num_m else raw
        author_m = re.match(r"^([A-Z][A-Za-z'’\-]+)", rest)
        year_m = re.search(r"\((\d{4}[a-z]?|n\.d\.)\)|\b(1[6-9]\d{2}|20\d{2})[a-z]?\b", rest)
        doi_m = DOI_RE.search(rest)
        url_m = URL_RE.search(rest)
        title = None
        if year_m:
            after = rest[year_m.end():].lstrip(" .),")
            t = re.split(r"(?<=[a-z0-9?])\.\s", after, maxsplit=1)[0].strip(" .\"“”")
            title = t[:300] if len(t) > 8 else None
        if title is None:
            q = re.search(r"[\"“](.+?)[\"”]", rest)
            if q:
                title = q.group(1).strip(" ,.")
        entries.append(
            ReferenceEntry(
                index=len(entries),
                start=s,
                end=e,
                text=raw,
                number=number,
                first_author=author_m.group(1) if author_m else None,
                year=(year_m.group(1) or year_m.group(2)) if year_m else None,
                doi=doi_m.group(1).rstrip(".,;)") if doi_m else None,
                url=url_m.group().rstrip(".,;)") if url_m else None,
                title_guess=title,
            )
        )
    return entries


def analyze_citations(text: str, sentences: list[Sentence]) -> CitationContext:
    bib = find_bibliography_start(text)
    body_end = bib if bib is not None else len(text)
    citations = extract_citations(text, body_end)
    references = parse_references(text, bib) if bib is not None else []
    quotes = find_quotes(text, body_end)
    issues: list[dict] = []

    by_number = {r.number: r for r in references if r.number is not None}
    by_author_year: dict[tuple[str, str], ReferenceEntry] = {}
    by_author: dict[str, list[ReferenceEntry]] = {}
    for r in references:
        if r.first_author:
            key = _surname_key(r.first_author)
            by_author.setdefault(key, []).append(r)
            if r.year:
                by_author_year[(key, r.year.lower())] = r

    for c in citations:
        ref = None
        if c.style == "numeric":
            for n in c.numbers:
                r = by_number.get(n)
                if r:
                    r.cited = True
                    ref = ref or r
            if ref is None and references:
                issues.append(_issue("missing_reference", c.start, c.end, f"Citation {c.text} has no matching numbered entry in the reference list."))
        else:
            items = c.items or ([(c.authors, c.year)] if c.authors else [])
            for authors, year in items:
                if not authors:
                    continue
                key = _surname_key(authors[0])
                r = by_author_year.get((key, year.lower())) if year else None
                if r is None and c.style == "mla":
                    r = (by_author.get(key) or [None])[0]
                if r is None and key in by_author and year:
                    cand = by_author[key][0]
                    issues.append(
                        _issue(
                            "year_mismatch",
                            c.start,
                            c.end,
                            f"{c.text} cites {authors[0]} ({year}), but the reference list has "
                            f"{cand.first_author} ({cand.year}).",
                        )
                    )
                    r = cand
                if r:
                    r.cited = True
                    ref = ref or r
                elif references:
                    who = f"{authors[0]}{' et al.' if len(authors) > 1 else ''}{', ' + year if year else ''}"
                    issues.append(_issue("missing_reference", c.start, c.end, f"No reference-list entry matches {who} in {c.text}."))
        c.reference_index = ref.index if ref else None

    for r in references:
        if not r.cited and citations:
            issues.append(_issue("uncited_reference", r.start, r.end, "This reference is never cited in the text.", severity="low"))
    if citations and bib is None:
        issues.append(_issue("no_reference_list", 0, 0, "The text contains citations but no reference list was found.", severity="medium"))
    if not citations and len(text.split()) > 400:
        issues.append(
            _issue("no_citations", 0, 0, "No in-text citations were detected in a document of this length.", severity="info")
        )

    styles = {c.style for c in citations}
    style = None
    if citations:
        counts = {s: sum(1 for c in citations if c.style == s) for s in styles}
        style = max(counts, key=counts.get)
        if "numeric" in styles and styles & {"author-year", "narrative"}:
            minority = min(counts["numeric"], counts.get("author-year", 0) + counts.get("narrative", 0))
            if minority >= 2:
                issues.append(_issue("mixed_styles", 0, 0, "Both numeric [1] and author-year (Smith, 2020) citation styles are used.", severity="low"))

    ctx = CitationContext(citations, references, quotes, bib, issues, style, [c.start for c in citations])
    for qs, qe in quotes:
        if text.count(" ", qs, qe) + 1 >= 8 and not ctx.has_citation_near(qs, qe, sentences):
            issues.append(
                _issue(
                    "quote_without_citation",
                    qs,
                    qe,
                    "This quotation has no citation in the same or the next sentence. Quoted material should name its source.",
                    severity="medium",
                )
            )
    issues.sort(key=lambda i: i["start"])
    return ctx


def _issue(kind: str, start: int, end: int, message: str, severity: str = "medium") -> dict:
    return {"type": kind, "start": start, "end": end, "message": message, "severity": severity}
