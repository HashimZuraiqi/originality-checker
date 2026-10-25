"""Sentence embedding providers.

All providers implement the same interface, so the semantic matcher does
not care which one runs:

* ``sbert``   - sentence-transformers (default model all-MiniLM-L6-v2). A
                contrastively trained sentence encoder; the strongest option.
                Needs the model weights (downloaded once from Hugging Face).
* ``wordvec`` - SIF-weighted average of static word vectors (Arora et al.,
                2017) from spaCy's en_core_web_md. Real distributional
                semantics (synonyms land close together), English only, but
                weaker on word order and negation than ``sbert``.
* ``lexical`` - TF-IDF over hashed word uni/bigrams. Not semantic at all: it
                only sees shared vocabulary. Used when neither model is
                available, and the report then says semantic analysis ran in
                degraded mode.

Thresholds differ by provider because the score distributions differ; the
values below come from the paraphrase benchmark in eval/ (see
docs/EVALUATION.md) and can be overridden.
"""

import json
import logging
import math
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from ...config import BASE_DIR, get_settings
from ..text import STOPWORDS, normalize_word

log = logging.getLogger(__name__)

WORDVEC_DIR = BASE_DIR / "data" / "models" / "wordvec"


def l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class EmbeddingProvider:
    name = "base"
    label = ""
    degraded = False
    paraphrase_threshold = 0.8  # cosine at/above which a sentence pair is a paraphrase candidate
    strong_threshold = 0.9  # cosine treated as strong evidence on its own

    def encode(self, texts: list[str]) -> np.ndarray:  # rows L2-normalised
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "degraded": self.degraded,
            "paraphrase_threshold": self.paraphrase_threshold,
            "strong_threshold": self.strong_threshold,
        }


class SbertProvider(EmbeddingProvider):
    name = "sbert"
    paraphrase_threshold = 0.78
    strong_threshold = 0.88

    def __init__(self, model_name: str, model=None):
        self.model_name = model_name
        self.label = f"Sentence-Transformers ({model_name.split('/')[-1]})"
        if model is None:
            from sentence_transformers import SentenceTransformer  # optional dependency

            model = SentenceTransformer(model_name, device="cpu")
        self._model = model

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        vecs = self._model.encode(texts, batch_size=64, convert_to_numpy=True, normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)


class WordVecProvider(EmbeddingProvider):
    name = "wordvec"
    label = "SIF-weighted word vectors (spaCy en_core_web_md)"
    paraphrase_threshold = 0.86
    strong_threshold = 0.93
    _SIF_A = 1e-3

    def __init__(self, vectors: np.ndarray, vocab: dict[str, int]):
        self._vectors = vectors
        self._vocab = vocab
        try:
            from wordfreq import word_frequency

            self._freq = lambda w: word_frequency(w, "en")
        except ImportError:  # pragma: no cover - wordfreq is a hard dependency
            self._freq = lambda w: 1e-5

    @classmethod
    def load(cls, directory: Path = WORDVEC_DIR) -> "WordVecProvider":
        vectors_file = directory / "vectors.npy"
        vocab_file = directory / "vocab.json"
        if not vectors_file.exists():
            build_wordvec_files(directory)
        vectors = np.load(vectors_file, mmap_mode="r")
        vocab = json.loads(vocab_file.read_text())
        return cls(vectors, vocab)

    def _sentence_vector(self, text: str) -> np.ndarray:
        acc = np.zeros(self._vectors.shape[1], dtype=np.float32)
        total = 0.0
        for m in re.finditer(r"[^\W\d_]+(?:'[^\W\d_]+)?", text):
            w = normalize_word(m.group())
            row = self._vocab.get(w)
            if row is None:
                continue
            weight = self._SIF_A / (self._SIF_A + self._freq(w))
            acc += weight * self._vectors[row]
            total += weight
        return acc / total if total else acc

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._vectors.shape[1]), dtype=np.float32)
        return l2_normalize(np.stack([self._sentence_vector(t) for t in texts]))


def build_wordvec_files(directory: Path = WORDVEC_DIR) -> None:
    """Convert spaCy's en_core_web_md vectors into a compact standalone
    format (vectors.npy + vocab.json) so spaCy is not needed at runtime."""
    try:
        import en_core_web_md  # noqa: F401
        from spacy.strings import StringStore
        from spacy.vectors import Vectors
    except ImportError as e:
        raise RuntimeError(
            "Word vectors are not installed. Run: pip install spacy "
            "https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/"
            "en_core_web_md-3.8.0-py3-none-any.whl"
        ) from e
    import en_core_web_md as pkg

    model_dir = next(Path(pkg.__file__).parent.glob("en_core_web_md-*")) / "vocab"
    vectors = Vectors().from_disk(model_dir)
    strings = StringStore().from_disk(model_dir / "strings.json")
    vocab: dict[str, int] = {}
    for s in strings:
        if not s.isalpha() or s.lower() != s or len(s) > 30:
            continue
        row = vectors.key2row.get(strings[s])
        if row is not None and row >= 0:
            vocab[s] = int(row)
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "vectors.npy", np.asarray(vectors.data, dtype=np.float32))
    (directory / "vocab.json").write_text(json.dumps(vocab))
    log.info("Built word vectors: %d words", len(vocab))


_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "ly", "s")


def light_stem(word: str) -> str:
    """Very small suffix stripper used for overlap measures (not a full
    Porter stemmer; it only needs to conflate plural/tense variants)."""
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def content_stems(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"[^\W_]+", text):
        w = normalize_word(m.group())
        if w not in STOPWORDS and len(w) > 2 and not w.isdigit():
            out.append(light_stem(w))
    return out


class LexicalProvider(EmbeddingProvider):
    name = "lexical"
    label = "TF-IDF word overlap (no semantic model available)"
    degraded = True
    paraphrase_threshold = 0.55
    strong_threshold = 0.75
    DIM = 1 << 13

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.DIM), dtype=np.float32)
        docs = []
        df: Counter[int] = Counter()
        for t in texts:
            stems = content_stems(t)
            feats = stems + [a + " " + b for a, b in zip(stems, stems[1:], strict=False)]
            counts = Counter(hash(f) % self.DIM for f in feats)
            docs.append(counts)
            df.update(counts.keys())
        n = len(texts)
        m = np.zeros((n, self.DIM), dtype=np.float32)
        for i, counts in enumerate(docs):
            for f, c in counts.items():
                m[i, f] = (1 + math.log(c)) * (math.log((1 + n) / (1 + df[f])) + 1)
        return l2_normalize(m)


class Reranker:
    """Optional cross-encoder that rescores candidate sentence pairs jointly
    (more accurate than comparing two independent embeddings)."""

    name = "cross-encoder"

    def __init__(self, model_name: str = "cross-encoder/stsb-distilroberta-base", model=None):
        if model is None:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(model_name, device="cpu")
        self._model = model
        self.model_name = model_name

    def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        return [float(x) for x in self._model.predict(pairs, batch_size=32)]


_provider_lock = threading.Lock()
_provider: EmbeddingProvider | None = None
_reranker: Reranker | None = None
_reranker_tried = False


def get_provider() -> EmbeddingProvider:
    """Pick the best available provider once per process."""
    global _provider
    with _provider_lock:
        if _provider is not None:
            return _provider
        choice = get_settings().embedding_provider
        errors = []
        if choice in ("auto", "sbert"):
            try:
                _provider = SbertProvider(get_settings().embedding_model)
                return _provider
            except Exception as e:  # noqa: BLE001 - import or download failure
                errors.append(f"sbert: {e}")
        if choice in ("auto", "wordvec"):
            try:
                _provider = WordVecProvider.load()
                return _provider
            except Exception as e:  # noqa: BLE001
                errors.append(f"wordvec: {e}")
        if errors:
            log.warning("Semantic models unavailable, using lexical fallback: %s", "; ".join(errors))
        _provider = LexicalProvider()
        return _provider


def get_reranker() -> Reranker | None:
    global _reranker, _reranker_tried
    with _provider_lock:
        if not _reranker_tried:
            _reranker_tried = True
            if get_settings().embedding_provider in ("auto", "sbert"):
                try:
                    _reranker = Reranker()
                except Exception as e:  # noqa: BLE001
                    log.info("Cross-encoder reranker unavailable: %s", e)
        return _reranker


def set_provider(provider: EmbeddingProvider | None, reranker: Reranker | None = None) -> None:
    """Test hook / explicit configuration."""
    global _provider, _reranker, _reranker_tried
    with _provider_lock:
        _provider = provider
        _reranker = reranker
        _reranker_tried = reranker is not None or provider is not None
