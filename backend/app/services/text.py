"""Text normalisation and segmentation with character offsets.

Every analysis module reports findings as [start, end) character offsets
into the same normalised document text, so the UI can highlight them
without re-tokenising.
"""

import re
import unicodedata
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)

# Abbreviations that end with a period but do not end a sentence.
_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "al", "cf", "vs", "fig", "figs", "eq", "eqs", "dr", "mr", "mrs", "ms", "prof",
    "st", "no", "vol", "pp", "p", "ch", "sec", "ed", "eds", "approx", "inc", "ltd", "jr", "sr", "dept",
    "univ", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "u.s",
    "u.k", "ph.d", "resp", "viz",
}

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being
    below between both but by can could did do does doing down during each either few for from further had
    has have having he her here hers herself him himself his how however i if in into is it its itself just
    may me might more most must my myself neither no nor not now of off on once only or other our ours
    ourselves out over own same shall she should so some such than that the their theirs them themselves
    then there these they this those through thus to too under until up upon us very was we were what when
    where whether which while who whom whose why will with within without would yet you your yours yourself
    yourselves""".split()
)


def normalize_document(text: str) -> str:
    """Canonical form used for storage and analysis: NFC, unified newlines,
    no control characters, collapsed runs of blank lines and spaces."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = text.replace("­", "")  # soft hyphen
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def normalize_word(word: str) -> str:
    return unicodedata.normalize("NFKC", word).lower().replace("’", "'")


@dataclass(slots=True)
class Token:
    word: str  # normalised form
    start: int
    end: int


@dataclass(slots=True)
class Sentence:
    index: int
    start: int
    end: int
    text: str
    paragraph: int = 0


@dataclass(slots=True)
class Paragraph:
    index: int
    start: int
    end: int
    text: str
    sentences: list[Sentence] = field(default_factory=list)


def tokenize(text: str) -> list[Token]:
    return [Token(normalize_word(m.group()), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def word_count(text: str) -> int:
    return sum(1 for _ in WORD_RE.finditer(text))


def content_words(text: str) -> list[str]:
    return [t.word for t in tokenize(text) if t.word not in STOPWORDS and len(t.word) > 2 and not t.word.isdigit()]


_SENT_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+|$)")


def _is_abbreviation(text: str, dot_pos: int) -> bool:
    # Word immediately before the terminal punctuation.
    m = re.search(r"([A-Za-z][A-Za-z.]*)$", text[:dot_pos])
    if not m:
        return False
    word = m.group(1).lower().rstrip(".")
    if word in _ABBREVIATIONS:
        return True
    # Single initials such as "J. Smith".
    return len(word) == 1 and word.isalpha() and text[dot_pos : dot_pos + 1] == "."


def split_sentences(text: str, offset: int = 0) -> list[tuple[int, int]]:
    """Return sentence spans [start, end) (relative to the original string
    plus `offset`)."""
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    while start < n and text[start].isspace():
        start += 1
    for m in _SENT_END.finditer(text):
        end = m.end()
        punct_pos = m.start()
        if text[punct_pos] == "." and _is_abbreviation(text, punct_pos):
            continue
        # Decimal numbers ("3.5") never match because the lookahead needs
        # whitespace. Require the next sentence to start with an uppercase
        # letter, digit, quote or bracket so "e.g. the" stays joined.
        nxt = end
        while nxt < n and text[nxt].isspace():
            nxt += 1
        if nxt < n and not (text[nxt].isupper() or text[nxt].isdigit() or text[nxt] in "\"'“‘([" ):
            continue
        if end > start:
            spans.append((start + offset, end + offset))
        start = nxt
    if start < n and text[start:].strip():
        spans.append((start + offset, len(text.rstrip()) + offset))
    return spans


def segment(text: str) -> list[Paragraph]:
    """Split into paragraphs (blank-line or single-newline separated blocks)
    and sentences, keeping offsets into `text`."""
    paragraphs: list[Paragraph] = []
    sent_index = 0
    for m in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", text):
        block = m.group()
        if not block.strip():
            continue
        lead = len(block) - len(block.lstrip())
        p_start = m.start() + lead
        p_end = m.start() + len(block.rstrip())
        para = Paragraph(index=len(paragraphs), start=p_start, end=p_end, text=text[p_start:p_end])
        for s, e in split_sentences(text[p_start:p_end], p_start):
            para.sentences.append(Sentence(sent_index, s, e, text[s:e], para.index))
            sent_index += 1
        paragraphs.append(para)
    return paragraphs


def all_sentences(paragraphs: list[Paragraph]) -> list[Sentence]:
    return [s for p in paragraphs for s in p.sentences]
