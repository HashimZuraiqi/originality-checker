"""AI-writing indicator analysis for the report.

Output is a *banded* verdict, never an "X% written by AI" figure:

* insufficient_text - too short for any meaningful estimate.
* few_indicators    - the stylometric profile looks typical of human writing.
* inconclusive      - mixed or moderate signals (the honest answer most of
                      the time for edited or formal text).
* many_indicators   - several independent signals typical of generated text.

Thresholds were chosen on the validation data to keep the false-positive
rate on human writing low (see docs/EVALUATION.md); a "many indicators"
result is still not proof of authorship.
"""

from ..text import Paragraph, word_count
from .features import FEATURE_INFO, extract_features, marker_spans
from .model import load_model, transform

MIN_DOC_WORDS = 150
MIN_PARAGRAPH_WORDS = 80

CAVEATS = [
    "AI-writing indicators are statistical patterns, not proof. No detector can establish who wrote a text.",
    "Formal, formulaic or non-native English writing can share these patterns and be flagged incorrectly "
    "(Liang et al., 2023, showed widely used detectors misclassify non-native writers' essays).",
    "Heavily edited AI text and AI-polished human text both fall between the categories.",
    "Use this result only as a prompt for a conversation about the writing process, never as the sole basis for a decision.",
]

VERDICT_LABELS = {
    "insufficient_text": "Not enough text to analyse",
    "few_indicators": "Few AI-writing indicators",
    "inconclusive": "Inconclusive",
    "many_indicators": "Many AI-writing indicators",
}


def _band(p: float, low: float, high: float) -> str:
    if p >= high:
        return "many_indicators"
    if p <= low:
        return "few_indicators"
    return "inconclusive"


def analyze_ai_writing(text: str, paragraphs: list[Paragraph], lang: str = "en", exclude_until: int | None = None) -> dict:
    body = text[:exclude_until] if exclude_until else text
    body_paras = [p for p in paragraphs if exclude_until is None or p.start < exclude_until]
    words = word_count(body)
    model = load_model()
    result: dict = {
        "verdict": "insufficient_text",
        "label": VERDICT_LABELS["insufficient_text"],
        "caveats": CAVEATS,
        "features": [],
        "paragraphs": [],
        "markers": [],
        "language_supported": lang == "en",
        "model": (model.meta if model else None),
    }
    if lang != "en":
        result["verdict"] = "insufficient_text"
        result["label"] = "Not available for this language"
        result["explanation"] = "The AI-writing indicators were developed and validated for English text only."
        return result
    if model is None:
        result["label"] = "AI-writing model not trained"
        result["explanation"] = "Run eval/ai_detection.py --train to create weights.json."
        return result

    low, high = model.meta.get("threshold_low", 0.25), model.meta.get("threshold_high", 0.85)
    feats = extract_features(body, body_paras, lang)
    x = transform(feats)
    p = float(model.predict_proba(x[None, :])[0])
    contrib = model.contributions(x)
    typical = model.meta.get("typical", {})
    result["features"] = [
        {
            "name": name,
            "label": FEATURE_INFO[name][0],
            "description": FEATURE_INFO[name][1],
            "value": feats[name],
            "typical_human": typical.get("human", {}).get(name),
            "typical_ai": typical.get("ai", {}).get(name),
            "contribution": round(contrib[name], 3),  # >0 pushes toward "AI-like"
        }
        for name in sorted(FEATURE_INFO, key=lambda n: -abs(contrib[n]))
    ]
    result["markers"] = [{"start": a, "end": b, "text": t} for a, b, t in marker_spans(body)][:200]

    if words < MIN_DOC_WORDS:
        result["explanation"] = f"The text has {words} words; at least {MIN_DOC_WORDS} are needed for a meaningful estimate."
        return result

    verdict = _band(p, low, high)
    # Long documents get paragraph-level estimates; if paragraphs disagree
    # strongly the honest document-level answer is "inconclusive" (possible
    # mixed authorship or editing).
    para_results = []
    for para in body_paras:
        if word_count(para.text) < MIN_PARAGRAPH_WORDS:
            continue
        pf = extract_features(para.text, None, lang)
        px = transform(pf)
        pp = float(model.predict_proba(px[None, :])[0])
        pc = model.contributions(px)
        top = [FEATURE_INFO[k][0] for k, v in sorted(pc.items(), key=lambda kv: -kv[1]) if v > 0.3][:3]
        para_results.append(
            {
                "index": para.index,
                "start": para.start,
                "end": para.end,
                "verdict": _band(pp, low, high),
                "score": round(pp, 3),
                "top_indicators": top,
            }
        )
    result["paragraphs"] = para_results
    flagged = sum(1 for r in para_results if r["verdict"] == "many_indicators")
    clean = sum(1 for r in para_results if r["verdict"] == "few_indicators")
    mixed = len(para_results) >= 4 and flagged >= 2 and clean >= 2
    if mixed and verdict != "inconclusive":
        verdict = "inconclusive"
        result["mixed_signals"] = True

    pushing = [f["label"] for f in result["features"] if f["contribution"] > 0.3][:3]
    against = [f["label"] for f in result["features"] if f["contribution"] < -0.3][:3]
    explanation = {
        "few_indicators": "The writing style is consistent with typical human writing on the measured indicators.",
        "inconclusive": "The indicators point in different directions or are only moderately elevated, so no reliable conclusion is possible.",
        "many_indicators": "Several independent style indicators are typical of AI-generated text. This is not proof of AI use.",
    }[verdict]
    if mixed:
        explanation += " Some paragraphs look very different from others, which can happen with mixed authorship or heavy editing."
    result.update(
        {
            "verdict": verdict,
            "label": VERDICT_LABELS[verdict],
            "score": round(p, 3),
            "score_note": "Model output on a 0-1 scale, calibrated only on the validation corpus described in the methodology. It is not the probability that AI wrote the text.",
            "explanation": explanation,
            "pushing_up": pushing,
            "pushing_down": against,
            "words_analyzed": words,
        }
    )
    return result
