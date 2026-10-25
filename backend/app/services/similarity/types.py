from dataclasses import dataclass, field


@dataclass
class SourceDoc:
    """A candidate source the document is compared against.

    `text_kind` records how much of the source we could actually read:
    "full_text" (whole article/page), "abstract" or "snippet". Matches
    against an abstract only prove overlap with the abstract.
    """

    id: str
    title: str
    text: str
    kind: str  # web | academic | encyclopedia | library | peer
    provider: str
    text_kind: str = "full_text"
    url: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    license: str | None = None
    found_by: list[str] = field(default_factory=list)  # query ids that surfaced it

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "provider": self.provider,
            "text_kind": self.text_kind,
            "url": self.url,
            "doi": self.doi,
            "authors": self.authors[:6],
            "year": self.year,
            "license": self.license,
        }


@dataclass
class Passage:
    """A contiguous region of the document matched to one source."""

    source_id: str
    start: int  # char offsets in the document
    end: int
    words: int  # document words in the region
    matched_words: int  # words covered by matching shingles / aligned sentences
    method: str  # exact | near_exact | semantic
    score: float  # exact: coverage ratio; semantic: similarity (provider scale)
    source_excerpt: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    evidence: dict = field(default_factory=dict)
