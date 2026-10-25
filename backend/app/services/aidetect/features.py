"""Stylometric features associated with machine-generated text.

Each feature is computable without a neural language model, explainable to
a non-specialist, and grounded in published observations:

* burstiness        - human writers vary sentence length more (Gehrmann et al.
                      2019 "GLTR"; the "burstiness" notion popularised by GPTZero).
* lexical diversity - MTLD (McCarthy & Jarvis 2010), length-robust.
* word commonness   - LLM sampling favours high-probability (common) words;
                      measured with corpus word frequencies (wordfreq).
* marker vocabulary - words whose frequency rose sharply after ChatGPT's
                      release (Kobak et al. 2024 "Delving into ChatGPT usage in
                      academic writing through excess vocabulary"; Liang et al.
                      2024), e.g. "delve", "pivotal", "showcasing".
* formulaic transitions and sentence openers ("Moreover,", "Furthermore,").
* paragraph uniformity, n-gram repetition, punctuation habits, typo-like
  out-of-vocabulary words (people make typos; LLMs rarely do).

None of these features is evidence on its own. Several are also typical of
non-native or highly formal human writing, which is why the classifier's
output is reported with an inconclusive band and caveats.
"""

import math
import re
import statistics
from collections import Counter

from ..text import STOPWORDS, Paragraph, normalize_word, split_sentences, tokenize

try:
    from wordfreq import zipf_frequency
except ImportError:  # pragma: no cover
    def zipf_frequency(word: str, lang: str) -> float:
        return 4.0

MARKER_WORDS = {
    "delve", "delves", "delving", "delved", "tapestry", "testament", "intricate", "intricacies", "multifaceted",
    "pivotal", "underscore", "underscores", "underscoring", "showcase", "showcases", "showcasing", "realm",
    "landscape", "navigate", "navigating", "foster", "fostering", "leverage", "leveraging", "seamless",
    "seamlessly", "robust", "crucial", "paramount", "meticulous", "meticulously", "commendable", "noteworthy",
    "nuanced", "holistic", "synergy", "unwavering", "elevate", "harness", "harnessing", "unlock", "unleash",
    "embark", "endeavor", "endeavour", "bustling", "vibrant", "invaluable", "groundbreaking", "transformative",
    "furthermore", "moreover", "additionally", "notably", "intriguing", "streamline", "streamlining",
    "boasts", "garner", "garnered", "showcased",
}
MARKER_PHRASES = [
    "it is important to note", "it's important to note", "it is worth noting", "plays a crucial role",
    "plays a pivotal role", "in today's", "in the realm of", "a testament to", "serves as a",
    "the landscape of", "a deeper understanding", "sheds light on", "paving the way",
    "navigate the complexities", "in an era of", "rich tapestry", "stands as a", "valuable insights",
    "fostering a",
]  # phrases common in ordinary student prose ("in conclusion", "not only") are excluded
# Openers over-used by LLMs. Ordinary academic connectives ("however",
# "therefore", "thus") are deliberately excluded: careful human writers use
# them constantly and counting them would raise false positives.
TRANSITIONS = {
    "moreover", "furthermore", "additionally", "overall", "ultimately", "notably", "importantly",
    "firstly", "secondly", "lastly", "conversely",
}
CONTRACTION_RE = re.compile(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", re.I)

FEATURE_INFO = {
    "burstiness": ("Sentence-length variation", "How much sentence lengths vary (coefficient of variation). Human writing usually varies more.", "low"),
    "mean_sentence_length": ("Average sentence length", "Average words per sentence.", "neutral"),
    "mtld": ("Vocabulary diversity (MTLD)", "Measure of textual lexical diversity; low values mean more repeated vocabulary.", "neutral"),
    "commonness": ("Word commonness", "Average corpus frequency (Zipf scale) of content words. Higher means more common, predictable words.", "high"),
    "marker_rate": ("AI-associated vocabulary", "Words and phrases per 1,000 words that became far more frequent after LLMs appeared (e.g. 'delve', 'pivotal', 'it is important to note').", "high"),
    "transition_opener_rate": ("Formulaic sentence openers", "Share of sentences starting with a transition such as 'Moreover,' or 'Furthermore,'.", "high"),
    "paragraph_uniformity": ("Paragraph length uniformity", "1 minus the variation in paragraph lengths; uniform paragraphs are common in generated text.", "high"),
    "repetition": ("Repeated phrasing", "Share of 3-word sequences that occur more than once.", "neutral"),
    "contraction_rate": ("Contractions", "Contractions (don't, it's) per 100 words; informal human writing uses more.", "low"),
    "oov_rate": ("Typos / unusual words", "Share of lowercase words not found in a large word list. Typos are a human trait; rare jargon also counts.", "low"),
    "punctuation_variety": ("Punctuation variety", "Share of sentences using ; : ( ) or ? — varied punctuation is more typical of human writing.", "low"),
    "emdash_rate": ("Em dash use", "Em dashes per 100 words.", "high"),
}
FEATURE_NAMES = list(FEATURE_INFO)


def mtld(words: list[str], threshold: float = 0.72) -> float:
    def one_pass(seq: list[str]) -> float:
        factors, types, count = 0.0, set(), 0
        for w in seq:
            count += 1
            types.add(w)
            if len(types) / count <= threshold:
                factors += 1
                types, count = set(), 0
        if count:
            ttr = len(types) / count
            factors += (1 - ttr) / (1 - threshold) if ttr < 1 else 0
        return len(seq) / factors if factors else float(len(seq))

    if len(words) < 10:
        return float(len(words))
    return (one_pass(words) + one_pass(words[::-1])) / 2


def extract_features(text: str, paragraphs: list[Paragraph] | None = None, lang: str = "en") -> dict[str, float]:
    tokens = tokenize(text)
    words = [t.word for t in tokens]
    n = max(1, len(words))
    sent_spans = split_sentences(text)
    sent_lens = [len(tokenize(text[a:b])) for a, b in sent_spans]
    sent_lens = [x for x in sent_lens if x > 0] or [n]
    mean_len = statistics.fmean(sent_lens)
    burst = statistics.pstdev(sent_lens) / mean_len if len(sent_lens) > 1 and mean_len else 0.0

    content = [w for w in words if w not in STOPWORDS and w.isalpha() and len(w) > 2]
    zl = "en" if lang not in ("en", "de", "fr", "es", "it", "pt", "nl") else lang
    zipfs = [zipf_frequency(w, zl) for w in content] or [4.0]
    commonness = statistics.fmean(z for z in zipfs if z > 0) if any(z > 0 for z in zipfs) else 0.0
    oov = sum(1 for w, z in zip(content, zipfs, strict=True) if z == 0) / max(1, len(content))

    lower = text.lower()
    markers = sum(1 for w in words if w in MARKER_WORDS) + sum(lower.count(p) for p in MARKER_PHRASES)

    openers = 0
    punct_sents = 0
    for a, b in sent_spans:
        s = text[a:b].strip()
        first = re.match(r"[A-Za-z']+", s)
        if first and normalize_word(first.group()) in TRANSITIONS and re.match(r"[A-Za-z' ]{1,25},", s):
            openers += 1
        if re.search(r"[;:()?]", s):
            punct_sents += 1
    n_sent = max(1, len(sent_spans))

    if paragraphs and len(paragraphs) >= 3:
        plens = [len(tokenize(p.text)) for p in paragraphs if p.text.strip()]
        pm = statistics.fmean(plens)
        uniformity = max(0.0, 1 - (statistics.pstdev(plens) / pm if pm else 0))
    else:
        uniformity = 0.5  # undefined for short texts: neutral value

    trigrams = Counter(tuple(words[i : i + 3]) for i in range(len(words) - 2))
    repeated = sum(c for c in trigrams.values() if c > 1)
    repetition = repeated / max(1, sum(trigrams.values()))

    return {
        "burstiness": round(burst, 4),
        "mean_sentence_length": round(mean_len, 3),
        "mtld": round(mtld(words), 3),
        "commonness": round(commonness, 4),
        "marker_rate": round(1000 * markers / n, 3),
        "transition_opener_rate": round(openers / n_sent, 4),
        "paragraph_uniformity": round(uniformity, 4),
        "repetition": round(repetition, 4),
        "contraction_rate": round(100 * len(CONTRACTION_RE.findall(text)) / n, 3),
        "oov_rate": round(oov, 4),
        "punctuation_variety": round(punct_sents / n_sent, 4),
        "emdash_rate": round(100 * text.count("—") / n, 3),
    }


def marker_spans(text: str) -> list[tuple[int, int, str]]:
    """Character spans of marker words/phrases, for highlighting."""
    spans = []
    for t in tokenize(text):
        if t.word in MARKER_WORDS:
            spans.append((t.start, t.end, text[t.start : t.end]))
    lower = text.lower()
    for p in MARKER_PHRASES:
        i = lower.find(p)
        while i != -1:
            spans.append((i, i + len(p), text[i : i + len(p)]))
            i = lower.find(p, i + 1)
    return sorted(set(spans))


def safe_log(x: float) -> float:
    return math.log(max(x, 1e-6))
