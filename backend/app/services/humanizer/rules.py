"""Deterministic, meaning-preserving editor (works with no AI service).

Each rule is a regular expression applied to the ORIGINAL text, so every
edit has an exact location and a reason the user can accept or reject.
Protected spans (citations, quotations, numbers, URLs/DOIs) are never
edited. What this editor cannot do - add explanation, restructure arguments,
improve coherence - is left to the LLM assistant and reported as such.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..citations import DOI_RE, URL_RE, extract_citations, find_quotes
from .changes import Change

MODES = {
    "natural": "Natural and conversational",
    "academic": "Formal academic",
    "professional": "Professional",
    "simple": "Simple and clear",
    "concise": "Concise",
    "detailed": "Detailed and explanatory",
}


@dataclass
class Rule:
    pattern: str
    replacement: str | Callable[[re.Match], str]
    reason: str
    modes: set[str]
    flags: int = re.IGNORECASE


def _keep_case(m: re.Match, rep: str) -> str:
    src = m.group(0)
    if src[:1].isupper() and rep:
        return rep[0].upper() + rep[1:]
    return rep


def _drop_opener(m: re.Match) -> str:
    # Remove a sentence-initial phrase and capitalise the next word (digits,
    # quotes and brackets are left untouched, so they are not consumed).
    nxt = m.group("next")
    return nxt.upper() if nxt else ""


ALL = set(MODES)
TIGHT = {"natural", "professional", "simple", "concise", "academic"}

WORDY = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bowing to the fact that\b", "because"),
    (r"\bin spite of the fact that\b", "although"),
    (r"\bdespite the fact that\b", "although"),
    (r"\bat this point in time\b", "now"),
    (r"\bat the present time\b", "currently"),
    (r"\bin the event that\b", "if"),
    (r"\bhas the ability to\b", "can"),
    (r"\bhave the ability to\b", "can"),
    (r"\bis able to\b", "can"),
    (r"\bare able to\b", "can"),
    (r"\bfor the purpose of\b", "for"),
    (r"\bwith regard to\b", "about"),
    (r"\bwith respect to\b", "regarding"),
    (r"\bin relation to\b", "about"),
    (r"\bthe majority of\b", "most"),
    (r"\ba large number of\b", "many"),
    (r"\ba significant number of\b", "many"),
    (r"\bprior to\b", "before"),
    (r"\bsubsequent to\b", "after"),
    (r"\bin the near future\b", "soon"),
    (r"\bon a daily basis\b", "daily"),
    (r"\bon a regular basis\b", "regularly"),
    (r"\beach and every\b", "every"),
    (r"\bend result\b", "result"),
    (r"\bpast history\b", "history"),
    (r"\bfuture plans\b", "plans"),
    (r"\bclose proximity\b", "proximity"),
    (r"\bbasic fundamentals\b", "fundamentals"),
    (r"\babsolutely essential\b", "essential"),
    (r"\bcompletely eliminate\b", "eliminate"),
    (r"\butili[sz]e\b", "use"),
    (r"\butili[sz]es\b", "uses"),
    (r"\butili[sz]ed\b", "used"),
    (r"\butili[sz]ing\b", "using"),
    (r"\butili[sz]ation\b", "use"),
    (r"\bcommence\b", "begin"),
    (r"\bcommenced\b", "began"),
]

CLICHES = [
    (r"\bdelve(?:s)? into\b", "explore"),
    (r"\bdelving into\b", "exploring"),
    (r"\ba testament to\b", "evidence of"),
    (r"\bplays? a (?:crucial|pivotal|vital|key) role in\b", "is central to"),
    (r"\bin today's (?:fast-paced|ever-changing|modern|digital) world,?\s*", "today, "),
    (r"\bnavigat(?:e|ing) the complexities of\b", "handling"),
    (r"\ba rich tapestry of\b", "a wide mix of"),
    (r"\btapestry of\b", "mix of"),
    (r"\bleverag(?:e|ing)\b", lambda m: "use" if m.group(0).lower().endswith("e") else "using"),
    (r"\bseamlessly\b", "smoothly"),
    (r"\bshowcas(?:e|es|ing)\b", lambda m: {"showcase": "show", "showcases": "shows", "showcasing": "showing"}[m.group(0).lower()]),
    (r"\bunderscor(?:e|es|ing)\b", lambda m: {"underscore": "highlight", "underscores": "highlights", "underscoring": "highlighting"}[m.group(0).lower()]),
    (r"\bin the realm of\b", "in"),
    (r"\bever-evolving\b", "changing"),
    (r"\bmultifaceted\b", "complex"),
    (r"\bsheds? light on\b", lambda m: "clarifies" if m.group(0).lower().startswith("sheds") else "clarify"),
    (r"\bpave the way for\b", "enable"),
    (r"\bpaving the way for\b", "enabling"),
]

OPENERS = [
    (r"(?:(?<=^)|(?<=\n)|(?<=[.!?]\s))(?:It is (?:important|worth|crucial) (?:to note|noting) that|It should be noted that|Needless to say,|It goes without saying that) (?:(?P<next>[^\W\d_])|(?=[\d\x22“(\[]))", "Remove an empty introductory phrase"),
    (r"(?:(?<=^)|(?<=\n)|(?<=[.!?]\s))(?:Moreover|Furthermore|Additionally|Ultimately|Notably|Importantly), (?:(?P<next>[^\W\d_])|(?=[\d\x22“(\[]))", "Drop a formulaic transition (the logical link is already clear)"),
]

CONTRACT = [
    (r"\bdo not\b", "don't"), (r"\bdoes not\b", "doesn't"), (r"\bdid not\b", "didn't"),
    (r"\bis not\b", "isn't"), (r"\bare not\b", "aren't"), (r"\bwas not\b", "wasn't"),
    (r"\bcannot\b", "can't"), (r"\bwill not\b", "won't"), (r"\bwould not\b", "wouldn't"),
    (r"\bit is\b", "it's"), (r"\bthat is\b", "that's"), (r"\bthere is\b", "there's"),
    (r"\bwe are\b", "we're"), (r"\bthey are\b", "they're"), (r"\byou are\b", "you're"),
]
EXPAND = [
    (r"\bdon't\b", "do not"), (r"\bdoesn't\b", "does not"), (r"\bdidn't\b", "did not"),
    (r"\bisn't\b", "is not"), (r"\baren't\b", "are not"), (r"\bwasn't\b", "was not"),
    (r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"), (r"\bwouldn't\b", "would not"),
    (r"\bit's\b", "it is"), (r"\bthat's\b", "that is"), (r"\bthere's\b", "there is"),
    (r"\bwe're\b", "we are"), (r"\bthey're\b", "they are"), (r"\byou're\b", "you are"),
    (r"\bshouldn't\b", "should not"), (r"\bcouldn't\b", "could not"), (r"\bhasn't\b", "has not"),
    (r"\bhaven't\b", "have not"),
]
INFORMAL = [
    (r"\bkids\b", "children"),
    (r"\ba lot of (?=\w+s\b)", "many "),
    (r"\ba lot of\b", "a great deal of"),
    (r"\blots of\b", "many"),
    (r"\bhuge\b", "substantial"),
    (r"\bstuff\b", "material"),
    (r"\bbasically,?\s*", ""),
    (r"\bkind of\b(?= [a-z]+(?:ed|ing|ly|ful|ous|ive|al)\b)", "somewhat"),
]


def _dedupe_word(m: re.Match) -> str:
    return m.group(1)


def _build_rules() -> list[Rule]:
    rules: list[Rule] = [
        # "the the" -> "the" ("had had" and "that that" can be correct English).
        Rule(r"\b(?!(?:had|that)\b)(\w+) \1\b", _dedupe_word, "Remove an accidentally repeated word", ALL),
    ]
    for pat, rep in WORDY:
        rules.append(Rule(pat, rep, "Replace a wordy phrase with a shorter equivalent", TIGHT))
    for pat, rep in CLICHES:
        rules.append(Rule(pat, rep, "Replace an overused stock phrase with plainer wording", {"natural", "simple", "concise", "professional", "academic"}))
    for pat, reason in OPENERS:
        rules.append(Rule(pat, _drop_opener, reason, TIGHT, flags=0))
    for pat, rep in CONTRACT:
        rules.append(Rule(pat, rep, "Use a contraction for a natural, conversational tone", {"natural"}))
    for pat, rep in EXPAND:
        rules.append(Rule(pat, rep, "Expand the contraction for formal writing", {"academic", "professional"}))
    for pat, rep in INFORMAL:
        rules.append(Rule(pat, rep, "Replace informal wording with a formal equivalent", {"academic", "professional"}))
    rules.append(
        Rule(
            r"\b(?P<art>[Aa]n? )?(?P<w>(?i:very|really|quite|extremely|totally|basically|actually|literally)) (?P<next>[a-z])",
            _drop_intensifier,
            "Remove an intensifier that adds little meaning (check the sentence still says what you mean)",
            {"concise"},
            flags=0,
        )
    )
    return rules


def _drop_intensifier(m: re.Match) -> str:
    """Drop the intensifier, fixing a/an and sentence-initial capitals."""
    nxt = m.group("next")
    art = m.group("art")
    if art:
        new_art = "an " if nxt in "aeiou" else "a "
        return (new_art.capitalize() if art[0].isupper() else new_art) + nxt
    return nxt.upper() if m.group("w")[0].isupper() else nxt


RULES = _build_rules()
NUMBER_RE = re.compile(r"\b\d[\d.,%]*\b")


def protected_spans(text: str) -> list[tuple[int, int]]:
    spans = list(find_quotes(text))
    spans += [(c.start, c.end) for c in extract_citations(text, len(text))]
    spans += [(m.start(), m.end()) for m in URL_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in DOI_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in NUMBER_RE.finditer(text)]
    return sorted(spans)


def _overlaps(a: int, b: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < e and b > s for s, e in spans)


@dataclass
class RewriteResult:
    text: str
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    engine: str = "rules"


def _split_long_sentences(text: str, taken: list[tuple[int, int]], protected: list[tuple[int, int]]) -> list[tuple[int, int, str, str]]:
    """Split sentences over 35 words at '; ' or ', and ' / ', but ' when both
    halves stay reasonably long."""
    edits = []
    for m in re.finditer(r"[^.!?]+[.!?]", text):
        sent = m.group()
        if len(sent.split()) <= 35:
            continue
        for sep_m in re.finditer(r"; |, (?:and|but|so) (?=[a-z])", sent):
            left = sent[: sep_m.start()]
            right = sent[sep_m.end() :]
            if len(left.split()) >= 10 and len(right.split()) >= 8:
                a = m.start() + sep_m.start()
                b = m.start() + sep_m.end()
                if not _overlaps(a, b, taken) and not _overlaps(a, b, protected):
                    edits.append((a, b + 1, ". " + text[b].upper(), "Split a very long sentence for readability"))
                break
    return edits


def rule_based_rewrite(text: str, mode: str) -> RewriteResult:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    result = RewriteResult(text=text)
    if mode == "detailed":
        result.notes.append(
            "Detailed and explanatory rewriting needs the AI writing assistant: adding explanation requires "
            "understanding the content, which the rule-based editor cannot do. No changes were made."
        )
        return result
    protected = protected_spans(text)
    taken: list[tuple[int, int]] = []
    edits: list[tuple[int, int, str, str]] = []
    for rule in RULES:
        if mode not in rule.modes:
            continue
        for m in re.finditer(rule.pattern, text, rule.flags):
            a, b = m.start(), m.end()
            if a == b or _overlaps(a, b, protected) or _overlaps(a, b, taken):
                continue
            rep = rule.replacement(m) if callable(rule.replacement) else _keep_case(m, rule.replacement)
            if rep == m.group(0):
                continue
            edits.append((a, b, rep, rule.reason))
            taken.append((a, b))
    if mode in ("simple", "concise", "professional"):
        edits.extend(_split_long_sentences(text, taken, protected))

    edits.sort()
    changes = [
        Change(id=f"r{i + 1}", start=a, end=b, original=text[a:b], replacement=rep, reason=reason, kind="rule")
        for i, (a, b, rep, reason) in enumerate(edits)
    ]
    from .changes import apply_changes

    result.changes = changes
    result.text = apply_changes(text, changes)
    result.notes.append(
        "Rule-based editor: tightens wording, replaces stock phrases and adjusts tone. "
        "It does not restructure paragraphs or improve argument flow; enable the AI writing assistant for that."
    )
    if not changes:
        result.notes.append("No rule-based improvements were found for this mode.")
    return result
