"""Language identification (langdetect: Naive Bayes over character n-grams,
the Shuyo 2010 language-detection library port; 55 languages)."""

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 0  # deterministic results

NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "nl": "Dutch", "ar": "Arabic", "tr": "Turkish", "ru": "Russian", "zh-cn": "Chinese", "ja": "Japanese",
    "ko": "Korean", "pl": "Polish", "sv": "Swedish", "id": "Indonesian", "hi": "Hindi", "fa": "Persian",
}


def detect_language(text: str) -> dict:
    sample = text[:20000]
    try:
        langs = detect_langs(sample)
    except LangDetectException:
        return {"code": None, "name": "Unknown", "confidence": 0.0}
    top = langs[0]
    return {"code": top.lang, "name": NAMES.get(top.lang, top.lang), "confidence": round(top.prob, 3)}
