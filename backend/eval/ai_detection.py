"""Train and evaluate the AI-writing indicator model.

    python -m eval.ai_detection --train            # build dataset, CV-evaluate, write weights.json
    python -m eval.ai_detection --external data.jsonl  # evaluate on a labelled set
                                                   # (one JSON object per line: {"text", "label": 0|1})
    python -m eval.ai_detection --download-hc3     # fetch the HC3 corpus (needs internet; English
                                                   # ChatGPT vs human answers) and evaluate on it

Development data:
* Human, all written before LLMs existed:
  - Brown corpus (1961 published American English; academic, essay,
    editorial and news samples), fetched from the NLTK data repository.
  - ELLIPSE corpus (essays by English-language learners, grades 8-12;
    Crossley et al. 2023, CC BY-NC-SA 4.0), fetched from GitHub. Used to
    measure false positives on non-native writers.
  - CPython standard-library docstrings (technical prose, PSF licence),
    cleaned of code markup.
* AI: eval/data/ai_samples.txt - LLM-generated technical paragraphs,
  encyclopedic essays and student essays on the ELLIPSE prompts (default
  voice and a "write like a high-school student" persona).

Raw third-party corpora are downloaded to eval/data/raw/ (git-ignored) and
are not redistributed with this repository.
"""

import argparse
import csv
import importlib
import inspect
import json
import pkgutil
import random
import re
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.aidetect.features import FEATURE_NAMES, extract_features  # noqa: E402
from app.services.aidetect.model import WEIGHTS_FILE, LogisticModel, fit_constrained, transform  # noqa: E402
from app.services.text import segment  # noqa: E402

DATA = Path(__file__).parent / "data"
RESULTS = Path(__file__).parent / "results"


def build_human_docstrings(out: Path = DATA / "human_docstrings.jsonl", min_words: int = 120) -> list[dict]:
    warnings.filterwarnings("ignore")
    skip = {"antigravity", "this", "idlelib", "tkinter", "turtle", "turtledemo", "test", "lib2to3", "ensurepip", "venv", "pydoc_data"}
    rows: dict[str, str] = {}
    for m in pkgutil.iter_modules():
        path = str(getattr(m.module_finder, "path", ""))
        if m.name.startswith("_") or m.name in skip or "site-packages" in path or "dist-packages" in path:
            continue
        try:
            mod = importlib.import_module(m.name)
        except BaseException:  # noqa: BLE001
            continue
        objs = [mod] + [
            o for _, o in inspect.getmembers(mod)
            if (inspect.isclass(o) or inspect.isfunction(o)) and getattr(o, "__module__", None) == mod.__name__
        ]
        for o in objs:
            doc = inspect.getdoc(o)
            if not doc:
                continue
            lines = [ln for ln in doc.splitlines() if not ln.strip().startswith((">>>", "...")) and not re.match(r"^\s{4,}", ln)]
            text = " ".join(" ".join(lines).split())
            if len(text.split()) >= min_words:
                rows[f"{mod.__name__}.{getattr(o, '__qualname__', '')}"] = text
    data = [{"id": k, "genre": "technical", "label": 0, "source": f"CPython {sys.version.split()[0]} stdlib docstring", "text": v} for k, v in sorted(rows.items())]
    out.write_text("\n".join(json.dumps(r) for r in data) + "\n")
    return data


def load_ai_samples(path: Path = DATA / "ai_samples.txt") -> list[dict]:
    rows, cur = [], None
    for line in path.read_text().splitlines():
        if line.startswith("### "):
            ident, genre = [x.strip() for x in line[4:].split("|")]
            cur = {"id": ident, "genre": genre, "label": 1, "source": "LLM-generated", "text": ""}
            rows.append(cur)
        elif line.startswith("#") or cur is None:
            continue
        else:
            cur["text"] += (" " if cur["text"] else "") + line.strip()
    return [r for r in rows if r["text"]]


RAW = DATA / "raw"
ELLIPSE_URL = "https://raw.githubusercontent.com/scrosseye/ELLIPSE-Corpus/main/ELLIPSE_Final_github_train.csv"
BROWN_URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/brown.zip"
BROWN_GENRES = {"j": "academic", "g": "academic", "b": "academic", "h": "academic", "a": "news"}


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    import httpx

    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
        r.raise_for_status()
        dest.write_bytes(b"".join(r.iter_bytes()))
    return dest


def _detokenize(tokens: list[str]) -> str:
    text = " ".join(tokens)
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    text = re.sub(r" ([,.;:?!)\u201d%])", r"\1", text)
    text = re.sub(r"([(\u201c$]) ", r"\1", text)
    text = re.sub(r" (n't|'s|'re|'ve|'ll|'d|'m)\b", r"\1", text)
    return text


def load_brown(per_file_words: int = 260, seed: int = 11) -> list[dict]:
    path = _download(BROWN_URL, RAW / "brown.zip")
    rows = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            base = name.rsplit("/", 1)[-1]
            if not re.fullmatch(r"c[a-r]\d\d", base) or base[1] not in BROWN_GENRES:
                continue
            raw = zf.read(name).decode("latin-1")
            paras = []
            for block in re.split(r"\n\s*\n", raw):
                toks = [t.rsplit("/", 1)[0] for t in block.split() if "/" in t]
                if toks:
                    paras.append(_detokenize(toks))
            # Take whole paragraphs from the start until ~per_file_words.
            picked, n = [], 0
            for ptxt in paras:
                picked.append(ptxt)
                n += len(ptxt.split())
                if n >= per_file_words:
                    break
            text = "\n\n".join(picked)
            if len(text.split()) >= 150:
                rows.append({"id": f"brown-{base}", "genre": BROWN_GENRES[base[1]], "label": 0, "source": "Brown corpus (1961)", "text": text})
    return rows


def load_ellipse(n: int = 160, seed: int = 5) -> list[dict]:
    path = _download(ELLIPSE_URL, RAW / "ellipse_train.csv")
    csv.field_size_limit(10**8)
    with path.open(encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if 150 <= len(r["full_text"].split()) <= 700]
    rng = random.Random(seed)
    rng.shuffle(rows)
    # Stratify by prompt so no single topic dominates.
    by_prompt: dict[str, list] = {}
    for r in rows:
        by_prompt.setdefault(r["prompt"], []).append(r)
    picked = []
    while len(picked) < n and any(by_prompt.values()):
        for p in sorted(by_prompt):
            if by_prompt[p] and len(picked) < n:
                picked.append(by_prompt[p].pop())
    return [
        {
            "id": f"ellipse-{r['text_id_kaggle']}",
            "genre": "student_essay",
            "label": 0,
            "source": "ELLIPSE (English-language learners)",
            "proficiency": float(r["Overall"]),
            "grade": r["grade"],
            "text": r["full_text"].strip(),
        }
        for r in picked
    ]


def load_docstrings(limit: int = 60) -> list[dict]:
    human_file = DATA / "human_docstrings.jsonl"
    rows = [json.loads(ln) for ln in human_file.read_text().splitlines() if ln.strip()] if human_file.exists() else build_human_docstrings()
    cleaned = []
    for r in rows:
        t = re.sub(r":[a-z]+:`~?([^`]+)`", r"\1", r["text"])
        t = t.replace("``", "").replace("`", "")
        t = re.sub(r"\b\w+\(\)", lambda m: m.group(0)[:-2], t)
        cleaned.append({**r, "text": t})
    random.Random(2).shuffle(cleaned)
    return cleaned[:limit]


def load_dataset() -> list[dict]:
    ai = load_ai_samples()
    for r in ai:
        if r["genre"] == "essay":
            r["genre"] = "academic"  # encyclopedic essays pair with Brown academic prose
        r["variant"] = "persona" if "persona" in r["id"] else "default"
    return load_brown() + load_ellipse() + load_docstrings() + ai


def featurize(texts: list[str]) -> np.ndarray:
    return np.stack([transform(extract_features(t, segment(t))) for t in texts])


def metrics(y: np.ndarray, p: np.ndarray, low: float, high: float) -> dict:
    pred_many = p >= high
    pred_few = p <= low
    tp = int(np.sum(pred_many & (y == 1)))
    fp = int(np.sum(pred_many & (y == 0)))
    fn = int(np.sum(~pred_many & (y == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # Expected calibration error, 10 bins.
    bins = np.linspace(0, 1, 11)
    ece = 0.0
    for a, b in zip(bins[:-1], bins[1:], strict=True):
        mask = (p >= a) & (p < b if b < 1 else p <= b)
        if mask.any():
            ece += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    auc = _auc(y, p)
    return {
        "n": int(len(y)),
        "precision_many": round(precision, 3),
        "recall_many": round(recall, 3),
        "f1_many": round(f1, 3),
        "false_positive_rate_many": round(fp / max(1, int(np.sum(y == 0))), 3),
        "human_marked_few": round(float(np.mean(pred_few[y == 0])) if np.any(y == 0) else 0.0, 3),
        "ai_marked_few": round(float(np.mean(pred_few[y == 1])) if np.any(y == 1) else 0.0, 3),
        "inconclusive_rate": round(float(np.mean(~pred_many & ~pred_few)), 3),
        "auc": round(auc, 3),
        "ece": round(float(ece), 3),
    }


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    wins = sum(float(np.sum(x > neg)) + 0.5 * float(np.sum(x == neg)) for x in pos)
    return wins / (len(pos) * len(neg))


def cross_validate(rows: list[dict], X: np.ndarray, folds: int = 5, seed: int = 7) -> tuple[np.ndarray, list]:
    y = np.array([r["label"] for r in rows])
    strata = [f"{r['label']}-{r['genre']}" for r in rows]
    rng = random.Random(seed)
    fold_of = np.zeros(len(rows), dtype=int)
    for s in set(strata):
        idx = [i for i, st in enumerate(strata) if st == s]
        rng.shuffle(idx)
        for k, i in enumerate(idx):
            fold_of[i] = k % folds
    oof = np.zeros(len(rows))
    for k in range(folds):
        tr, te = fold_of != k, fold_of == k
        mean, std, coef, b, _ = fit_constrained(X[tr], y[tr])
        model = LogisticModel(mean, std, coef, b, {})
        oof[te] = model.predict_proba(X[te])
    return oof, fold_of.tolist()


def choose_thresholds(y: np.ndarray, p: np.ndarray, target_fpr: float = 0.02, target_fnr_low: float = 0.05) -> tuple[float, float]:
    human = np.sort(p[y == 0])
    ai = np.sort(p[y == 1])
    # High threshold: at most target_fpr of human texts at or above it.
    high = float(np.quantile(human, 1 - target_fpr)) if len(human) else 0.85
    high = min(0.97, max(0.6, high + 1e-6))
    # Low threshold: at most target_fnr_low of AI texts at or below it.
    low = float(np.quantile(ai, target_fnr_low)) if len(ai) else 0.25
    low = max(0.03, min(0.4, low - 1e-6))
    if low >= high:
        low = high / 2
    return round(low, 3), round(high, 3)


def edited_variants(rows: list[dict]) -> list[dict]:
    """Human-edited AI text simulated with the project's rule-based editor,
    and mixed documents (human paragraph + AI paragraph)."""
    from app.services.humanizer.rules import rule_based_rewrite

    out = []
    ai = [r for r in rows if r["label"] == 1]
    human = [r for r in rows if r["label"] == 0]
    for r in ai:
        out.append({**r, "id": r["id"] + "-edited", "variant": "ai_edited", "text": rule_based_rewrite(r["text"], "natural").text})
    rng = random.Random(3)
    for r in ai[:20]:
        h = rng.choice(human)
        out.append({"id": r["id"] + "-mixed", "genre": "mixed", "label": 1, "variant": "mixed", "text": h["text"] + "\n\n" + r["text"]})
    return out


def train(args) -> None:
    rows = load_dataset()
    X = featurize([r["text"] for r in rows])
    y = np.array([r["label"] for r in rows])
    oof, _ = cross_validate(rows, X)
    low, high = choose_thresholds(y, oof)
    results = {"thresholds": {"low": low, "high": high}, "cross_validation": {"all": metrics(y, oof, low, high)}}
    for genre in sorted({r["genre"] for r in rows}):
        mask = np.array([r["genre"] == genre for r in rows])
        if len(set(y[mask])) > 1:
            results["cross_validation"][genre] = metrics(y[mask], oof[mask], low, high)
        else:
            results["cross_validation"][genre] = {
                "n": int(mask.sum()),
                "note": "single-class subset; only detection rate is meaningful",
                "marked_many": round(float(np.mean(oof[mask] >= high)), 3),
                "marked_few": round(float(np.mean(oof[mask] <= low)), 3),
            }

    # Non-native writers: false-positive rate by English proficiency (1-5 scale).
    ell = [i for i, r in enumerate(rows) if r.get("source", "").startswith("ELLIPSE")]
    bands = {"lower (<2.5)": lambda v: v < 2.5, "middle (2.5-3.5)": lambda v: 2.5 <= v < 3.5, "higher (>=3.5)": lambda v: v >= 3.5}
    results["non_native_by_proficiency"] = {}
    for band_name, test in bands.items():
        idx = [i for i in ell if test(rows[i]["proficiency"])]
        if idx:
            results["non_native_by_proficiency"][band_name] = {
                "n": len(idx),
                "false_positive_rate_many": round(float(np.mean(oof[idx] >= high)), 3),
                "marked_few": round(float(np.mean(oof[idx] <= low)), 3),
            }
    for variant in ("default", "persona"):
        idx = [i for i, r in enumerate(rows) if r["label"] == 1 and r.get("variant") == variant and r["genre"] == "student_essay"]
        if idx:
            results[f"ai_student_essay_{variant}_voice"] = {
                "n": len(idx),
                "recall_many": round(float(np.mean(oof[idx] >= high)), 3),
                "marked_few": round(float(np.mean(oof[idx] <= low)), 3),
            }

    mean, std, coef, b, dropped = fit_constrained(X, y)
    results["features_dropped_by_sign_constraint"] = dropped
    typical = {
        cls: {name: round(float(np.median([extract_features(r["text"])[name] for r in rows if r["label"] == lab])), 3) for name in FEATURE_NAMES}
        for cls, lab in (("human", 0), ("ai", 1))
    }
    model = LogisticModel(
        mean,
        std,
        coef,
        b,
        {
            "version": "stylometric-lr-1",
            "threshold_low": low,
            "threshold_high": high,
            "trained_on": f"{int(np.sum(y == 0))} human texts (Brown corpus, ELLIPSE learner essays, CPython docstrings) + {int(np.sum(y == 1))} LLM-generated texts",
            "cv_metrics": results["cross_validation"]["all"],
            "features_dropped": dropped,
            "typical": typical,
            "limitations": "AI samples come from a single LLM family; human essays are from US secondary-school English learners; "
            "performance on other generators, languages and genres is unmeasured.",
        },
    )

    # Robustness: edited AI and mixed documents, scored by the final model.
    variants = edited_variants(rows)
    Xv = featurize([r["text"] for r in variants])
    pv = model.predict_proba(Xv)
    for v in ("ai_edited", "mixed"):
        mask = np.array([r["variant"] == v for r in variants])
        results[f"robustness_{v}"] = {
            "n": int(mask.sum()),
            "marked_many": round(float(np.mean(pv[mask] >= high)), 3),
            "inconclusive": round(float(np.mean((pv[mask] > low) & (pv[mask] < high))), 3),
            "marked_few": round(float(np.mean(pv[mask] <= low)), 3),
            "note": "scored by the final model trained on unedited data (optimistic for in-distribution AI text)",
        }
    results["coefficients"] = {n: round(float(c), 3) for n, c in sorted(zip(FEATURE_NAMES, coef, strict=True), key=lambda kv: -abs(kv[1]))}
    WEIGHTS_FILE.write_text(json.dumps(model.to_json(), indent=2))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "ai_detection.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


def evaluate_external(path: Path) -> None:
    from app.services.aidetect.model import load_model

    model = load_model()
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    rows = [r for r in rows if len(r["text"].split()) >= 150]
    X = featurize([r["text"] for r in rows])
    y = np.array([int(r["label"]) for r in rows])
    p = model.predict_proba(X)
    out = metrics(y, p, model.meta["threshold_low"], model.meta["threshold_high"])
    print(json.dumps(out, indent=2))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"ai_detection_external_{path.stem}.json").write_text(json.dumps(out, indent=2))


def download_hc3(out: Path = DATA / "hc3_open_qa.jsonl") -> Path:
    import httpx

    url = "https://huggingface.co/datasets/Hello-SimpleAI/HC3/resolve/main/open_qa.jsonl"
    rows = []
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.strip():
                continue
            item = json.loads(line)
            for h in item.get("human_answers", []):
                rows.append({"text": h, "label": 0})
            for a in item.get("chatgpt_answers", []):
                rows.append({"text": a, "label": 1})
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--rebuild-human", action="store_true")
    ap.add_argument("--external", type=Path)
    ap.add_argument("--download-hc3", action="store_true")
    a = ap.parse_args()
    if a.rebuild_human:
        print(len(build_human_docstrings()), "human samples")
    if a.train:
        train(a)
    if a.download_hc3:
        evaluate_external(download_hc3())
    if a.external:
        evaluate_external(a.external)
