"""Writing-quality analysis: readability, clarity, concision and mechanics.

Built-in checks are rule-based and conservative (each rule targets a pattern
that is almost always worth a second look). If LANGUAGETOOL_URL points to a
LanguageTool server, its grammar and spelling matches are merged in.

The quality index is a transparent average of four subscores whose
formulas are returned with the result, so a reader can recompute them.
"""

import logging
import re

import httpx

from ..config import get_settings
from .citations import CitationContext
from .humanizer.rules import WORDY
from .text import Paragraph, Sentence, tokenize, word_count

log = logging.getLogger(__name__)

IRREGULAR_PARTICIPLES = (
    "made|done|seen|given|taken|known|shown|written|found|held|built|brought|thought|told|sent|left|kept|"
    "chosen|drawn|driven|eaten|fallen|forgotten|grown|hidden|led|lost|meant|paid|put|read|run|said|set|"
    "sold|spent|spoken|stolen|taught|understood|won|broken|begun|caught"
)
PASSIVE_RE = re.compile(rf"\b(am|is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w+ed|{IRREGULAR_PARTICIPLES})\b", re.I)
DOUBLE_WORD_RE = re.compile(r"\b(\w+)\s+\1\b", re.I)
A_AN_RE = re.compile(r"\b(a|an)\s+([A-Za-z][\w-]*)", re.I)
SPACE_BEFORE_PUNCT_RE = re.compile(r"\w \s*[,.;:!?](?=\s|$)")
MISSING_SPACE_RE = re.compile(r"[a-z]{2,}[,.;:][A-Z][a-z]")
CONFUSIONS = [
    (r"\b(could|should|would|must|might) of\b", lambda m: f"{m.group(1)} have", "“{0}” should be “{1}”."),
    (r"\balot\b", "a lot", "“alot” is not a word; write “a lot”."),
    (r"\birregardless\b", "regardless", "Use “regardless”."),
    (r"\bthen ever\b", "than ever", "Comparisons use “than”."),
    (r"\bmore (\w+) then\b", lambda m: f"more {m.group(1)} than", "Comparisons use “than”."),
    (r"\bless (\w+) then\b", lambda m: f"less {m.group(1)} than", "Comparisons use “than”."),
    (r"\bits' \b", "its ", "“its'” is not a word: use “its” (possessive) or “it's” (it is)."),
    (r"\bin regards to\b", "regarding", "Use “regarding” or “in regard to”."),
]
VOWEL_SOUND_EXCEPTIONS_AN = {"hour", "hours", "honest", "honestly", "honor", "honour", "honorable", "heir", "herb"}
# Words starting with a vowel letter but a consonant sound ("a union", "a one-off").
# "unin-/unim-" words (uninformed, unimportant) keep the vowel sound.
CONSONANT_SOUND_EXCEPTIONS_A = re.compile(r"^(uni(?![nm])|unanim|use|usu|uti|eu|one|once|ur[aeiou]|u[bcfgklmprstvz][aeiou])", re.I)
FILLERS = {"very", "really", "basically", "actually", "literally", "totally", "quite", "just"}


def _syllables(word: str) -> int:
    w = word.lower()
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def readability(text: str, sentences: list[Sentence]) -> dict:
    words = [t for t in tokenize(text) if t.word.isalpha()]
    n_words = max(1, len(words))
    n_sent = max(1, len(sentences))
    syll = sum(_syllables(text[t.start : t.end]) for t in words)
    asl = n_words / n_sent
    asw = syll / n_words
    fre = 206.835 - 1.015 * asl - 84.6 * asw
    fk = 0.39 * asl + 11.8 * asw - 15.59
    return {
        "flesch_reading_ease": round(fre, 1),
        "flesch_kincaid_grade": round(fk, 1),
        "avg_sentence_words": round(asl, 1),
        "avg_syllables_per_word": round(asw, 2),
        "sentences": n_sent,
        "words": n_words,
    }


def _finding(category: str, start: int, end: int, message: str, suggestion: str | None = None, severity: str = "info", replacement: str | None = None) -> dict:
    return {
        "kind": "quality",
        "category": category,
        "severity": severity,
        "start": start,
        "end": end,
        "message": message,
        "suggestion": suggestion,
        "replacement": replacement,
    }


def _languagetool(text: str, lang: str) -> list[dict]:
    url = get_settings().languagetool_url
    if not url:
        return []
    try:
        r = httpx.post(f"{url.rstrip('/')}/v2/check", data={"text": text[:100000], "language": lang or "auto"}, timeout=30)
        r.raise_for_status()
        out = []
        for m in r.json().get("matches", [])[:300]:
            reps = [x["value"] for x in m.get("replacements", [])[:3]]
            out.append(
                _finding(
                    "grammar" if m.get("rule", {}).get("issueType") != "misspelling" else "spelling",
                    m["offset"],
                    m["offset"] + m["length"],
                    m.get("message", "Possible issue"),
                    ("Suggestion: " + ", ".join(reps)) if reps else None,
                    "low",
                    reps[0] if reps else None,
                )
            )
        return out
    except Exception as e:  # noqa: BLE001 - optional engine
        log.warning("LanguageTool unavailable: %s", e)
        return []


def analyze_quality(text: str, paragraphs: list[Paragraph], sentences: list[Sentence], citations: CitationContext, lang: str | None) -> dict:
    body_end = citations.bibliography_start if citations.bibliography_start is not None else len(text)
    body_sents = [s for s in sentences if s.start < body_end]
    findings: list[dict] = []
    protected = list(citations.quotes) + [(c.start, c.end) for c in citations.citations]

    def free(a: int, b: int) -> bool:
        return b <= body_end and all(b <= s or a >= e for s, e in protected)

    long_count = passive_count = 0
    for s in body_sents:
        n = word_count(s.text)
        if n > 40:
            long_count += 1
            findings.append(_finding("long_sentence", s.start, s.end, f"This sentence has {n} words.", "Consider splitting it into two or three sentences so each makes one point.", "low"))
        for m in PASSIVE_RE.finditer(s.text):
            passive_count += 1
            if passive_count <= 25:
                a, b = s.start + m.start(), s.start + m.end()
                findings.append(_finding("passive_voice", a, b, "Passive voice.", "Passive voice is fine in methods sections; elsewhere an active verb is often clearer (who did what?)."))
        if s.text and s.text[0].islower() and s.text[0].isalpha():
            findings.append(_finding("capitalization", s.start, s.start + 1, "Sentence starts with a lowercase letter.", None, "low", s.text[0].upper()))

    # The sentence splitter keeps "end. lowercase" joined, so check it directly.
    for m in re.finditer(r"(?<![A-Z])(?<!\b[a-z])([a-z]{2,})[.!?] +([a-z])", text[:body_end]):
        word = m.group(1).lower()
        a = m.start(2)
        if word not in ("e.g", "i.e", "etc", "al", "vs", "cf") and free(a, a + 1):
            findings.append(_finding("capitalization", a, a + 1, "Sentence starts with a lowercase letter.", None, "low", text[a].upper()))
    for m in DOUBLE_WORD_RE.finditer(text[:body_end]):
        if m.group(1).lower() not in ("had", "that") and free(m.start(), m.end()):
            findings.append(_finding("repeated_word", m.start(), m.end(), f"Repeated word “{m.group(1)}”.", None, "medium", m.group(1)))
    for m in A_AN_RE.finditer(text[:body_end]):
        art, word = m.group(1), m.group(2)
        w = word.lower()
        vowel = w[0] in "aeiou"
        if art.lower() == "a" and vowel and not CONSONANT_SOUND_EXCEPTIONS_A.match(w) or art.lower() == "a" and w in VOWEL_SOUND_EXCEPTIONS_AN:
            fix = "An" if art[0].isupper() else "an"
        elif art.lower() == "an" and not vowel and w not in VOWEL_SOUND_EXCEPTIONS_AN and not word.isupper():
            fix = "A" if art[0].isupper() else "a"
        elif art.lower() == "an" and vowel and CONSONANT_SOUND_EXCEPTIONS_A.match(w):
            fix = "A" if art[0].isupper() else "a"
        else:
            continue
        if free(m.start(), m.end()):
            findings.append(_finding("article", m.start(), m.start() + len(art), f"Use “{fix}” before “{word}”.", None, "medium", fix))
    for m in SPACE_BEFORE_PUNCT_RE.finditer(text[:body_end]):
        a = m.start() + 1
        b = m.end()
        if free(a, b):
            findings.append(_finding("spacing", a, b, "Remove the space before the punctuation mark.", None, "low", text[a:b].strip()))
    for m in MISSING_SPACE_RE.finditer(text[:body_end]):
        if free(m.start(), m.end()) and not re.search(r"\w\.\w+\.", text[m.start() - 2 : m.end() + 3]):
            p = m.start() + re.search(r"[,.;:]", m.group()).start()
            findings.append(_finding("spacing", p, p + 2, "Add a space after the punctuation mark.", None, "low", text[p] + " " + text[p + 1]))
    for pat, rep, msg in CONFUSIONS:
        for m in re.finditer(pat, text[:body_end], re.I):
            if free(m.start(), m.end()):
                replacement = rep(m) if callable(rep) else rep
                findings.append(_finding("word_choice", m.start(), m.end(), msg.format(m.group(0), replacement), None, "medium", replacement))
    wordy_count = 0
    for pat, rep in WORDY:
        for m in re.finditer(pat, text[:body_end], re.I):
            if free(m.start(), m.end()):
                wordy_count += 1
                findings.append(_finding("wordiness", m.start(), m.end(), f"“{m.group(0)}” can usually be shortened to “{rep}”.", None, "info", rep))

    filler_count = sum(1 for t in tokenize(text[:body_end]) if t.word in FILLERS)
    for p in paragraphs:
        if p.start >= body_end:
            break
        n = word_count(p.text)
        if n > 250:
            findings.append(_finding("long_paragraph", p.start, min(p.end, p.start + 200), f"This paragraph has {n} words.", "Long paragraphs are hard to follow; consider splitting where the topic shifts.", "info"))

    findings.extend(f for f in _languagetool(text[:body_end], lang or "en") if free(f["start"], f["end"]))
    findings.sort(key=lambda f: (f["start"], f["end"]))
    # Remove exact duplicates (built-in and LanguageTool may agree).
    seen, unique = set(), []
    for f in findings:
        key = (f["start"], f["end"], f["category"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    for i, f in enumerate(unique):
        f["id"] = f"q-{i + 1}"

    read = readability(text[:body_end], body_sents)
    words = max(1, read["words"])
    n_sent = max(1, len(body_sents))
    mechanics_issues = sum(1 for f in unique if f["category"] in ("repeated_word", "article", "spacing", "word_choice", "capitalization", "grammar", "spelling"))
    pct_long = 100 * long_count / n_sent
    pct_passive = 100 * passive_count / n_sent
    fk = read["flesch_kincaid_grade"]
    subscores = {
        "mechanics": round(max(0.0, 100 - 1000 * mechanics_issues / words * 2.5), 1),
        "clarity": round(max(0.0, 100 - 1.5 * pct_long - 0.4 * pct_passive), 1),
        "concision": round(max(0.0, 100 - 1000 * (wordy_count + 0.5 * filler_count) / words * 2.0), 1),
        "readability": round(max(0.0, 100 - 8 * max(0.0, fk - 16, 9 - fk)), 1),
    }
    formulas = {
        "mechanics": "100 − 2.5 × (mechanical issues per 1,000 words)",
        "clarity": "100 − 1.5 × (% sentences over 40 words) − 0.4 × (% sentences with passive voice)",
        "concision": "100 − 2 × ((wordy phrases + 0.5 × filler words) per 1,000 words)",
        "readability": "100 − 8 × (grade levels outside the 9–16 range typical of academic prose; Flesch-Kincaid)",
    }
    return {
        "score": round(sum(subscores.values()) / 4, 1),
        "subscores": subscores,
        "formulas": formulas,
        "readability": read,
        "counts": {
            "long_sentences": long_count,
            "passive_sentences": passive_count,
            "wordy_phrases": wordy_count,
            "filler_words": filler_count,
            "mechanical_issues": mechanics_issues,
        },
        "findings": unique[:400],
        "engine": "built-in rules" + (" + LanguageTool" if get_settings().languagetool_url else ""),
    }
