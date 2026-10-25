"""Similarity-engine evaluation.

    python -m eval.similarity --calibrate     # Part A: MRPC sentence pairs, per embedding provider
    python -m eval.similarity --benchmark     # Part B: document-level passage detection
    python -m eval.similarity --all

Part A - paraphrase decision rule on the Microsoft Research Paraphrase
Corpus (Dolan & Brockett 2005). MRPC's negative pairs are topically related
news sentences that are NOT paraphrases, which is exactly the "same topic is
not copying" case. Thresholds are chosen on the train split only (precision-
weighted F0.5, because a false flag costs more than a miss) and reported on
the untouched test split, using the same rule as semantic.py.

Part B - end-to-end engine (exact + near-exact + semantic + classification)
on synthetic documents with known ground truth, built from the Brown corpus
(sources and distractors) and MRPC test pairs (real human paraphrases and
topical non-paraphrases). Source *retrieval* is not part of this benchmark;
it needs live services and is evaluated by eval/retrieval_live.py.
"""

import argparse
import json
import random
import re
import resource
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.citations import analyze_citations  # noqa: E402
from app.services.similarity import semantic  # noqa: E402
from app.services.similarity.embeddings import EmbeddingProvider, LexicalProvider, WordVecProvider, content_stems  # noqa: E402
from app.services.similarity.engine import analyze_similarity  # noqa: E402
from app.services.similarity.types import SourceDoc  # noqa: E402
from app.services.text import all_sentences, normalize_document, segment, word_count  # noqa: E402
from eval.ai_detection import BROWN_URL, RAW, _detokenize, _download  # noqa: E402

RESULTS = Path(__file__).parent / "results"
MRPC_URLS = {
    "train": "https://raw.githubusercontent.com/wasiahmad/paraphrase_identification/master/dataset/msr-paraphrase-corpus/msr_paraphrase_train.txt",
    "test": "https://raw.githubusercontent.com/wasiahmad/paraphrase_identification/master/dataset/msr-paraphrase-corpus/msr_paraphrase_test.txt",
}


def load_mrpc(split: str) -> list[tuple[int, str, str]]:
    path = _download(MRPC_URLS[split], RAW / f"mrpc_{split}.txt")
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 5:
            rows.append((int(parts[0]), parts[3], parts[4]))
    return rows


def providers() -> dict[str, EmbeddingProvider]:
    out: dict[str, EmbeddingProvider] = {"lexical": LexicalProvider()}
    try:
        out["wordvec"] = WordVecProvider.load()
    except Exception as e:  # noqa: BLE001
        print("wordvec unavailable:", e)
    try:
        from app.services.similarity.embeddings import SbertProvider

        out["sbert"] = SbertProvider("sentence-transformers/all-MiniLM-L6-v2")
    except Exception as e:  # noqa: BLE001
        print("sbert unavailable:", e)
    return out


def _overlap(a: str, b: str) -> float:
    sa, sb = frozenset(content_stems(a)), frozenset(content_stems(b))
    return semantic._overlap(sa, sb)


def _rule(cos: np.ndarray, ov: np.ndarray, lr: np.ndarray, t: float, strong: float) -> np.ndarray:
    return (cos >= t) & ((cos >= strong) | (ov >= semantic.MIN_OVERLAP)) & (lr >= 0.4) & (lr <= 2.5)


def _prf(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
        "false_positive_rate": round(fp / max(1, int(np.sum(y == 0))), 3),
        "accuracy": round(float(np.mean(pred == (y == 1))), 3),
    }


def _auc(y: np.ndarray, s: np.ndarray) -> float:
    pos, neg = s[y == 1], s[y == 0]
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _pair_features(provider: EmbeddingProvider, rows):
    a = [r[1] for r in rows]
    b = [r[2] for r in rows]
    va, vb = provider.encode(a), provider.encode(b)
    cos = np.sum(va * vb, axis=1)
    ov = np.array([_overlap(x, z) for x, z in zip(a, b, strict=True)])
    lr = np.array([max(1, word_count(x)) / max(1, word_count(z)) for x, z in zip(a, b, strict=True)])
    y = np.array([r[0] for r in rows])
    return y, cos, ov, lr


def calibrate() -> dict:
    train, test = load_mrpc("train"), load_mrpc("test")
    results = {"dataset": f"MRPC train {len(train)} / test {len(test)} pairs (positive rate test {np.mean([r[0] for r in test]):.2f})"}
    for name, prov in providers().items():
        y, cos, ov, lr = _pair_features(prov, train)
        grid = np.round(np.arange(0.30, 0.99, 0.01), 2)
        # Strong threshold: smallest cosine whose precision on train is >= 0.95.
        strong = 0.99
        for t in grid:
            sel = cos >= t
            if sel.sum() >= 20 and np.mean(y[sel]) >= 0.95:
                strong = float(t)
                break
        best_t, best_f = grid[0], -1.0
        for t in grid:
            if t > strong:
                break
            m = _prf(y, _rule(cos, ov, lr, t, strong))
            p, r = m["precision"], m["recall"]
            f05 = 1.25 * p * r / (0.25 * p + r) if p + r else 0
            if f05 > best_f:
                best_t, best_f = float(t), f05
        yt, ct, ot, lt = _pair_features(prov, test)
        current = (prov.paraphrase_threshold, prov.strong_threshold)
        results[name] = {
            "calibrated": {"paraphrase_threshold": best_t, "strong_threshold": strong},
            "previous": {"paraphrase_threshold": current[0], "strong_threshold": current[1]},
            "test_auc_cosine": round(_auc(yt, ct), 3),
            "test_rule_calibrated": _prf(yt, _rule(ct, ot, lt, best_t, strong)),
            "test_rule_previous": _prf(yt, _rule(ct, ot, lt, *current)),
            "test_cosine_only_at_calibrated": _prf(yt, ct >= best_t),
        }
    results["baselines"] = {
        "always_paraphrase": _prf(np.array([r[0] for r in test]), np.ones(len(test), dtype=bool)),
        "word_overlap_0.5": _prf(np.array([r[0] for r in test]), np.array([_overlap(r[1], r[2]) >= 0.5 for r in test])),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "paraphrase_calibration.json").write_text(json.dumps(results, indent=2))
    return results


# ------------------------------------------------------------- Part B
def _brown_files() -> dict[str, str]:
    path = _download(BROWN_URL, RAW / "brown.zip")
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            base = name.rsplit("/", 1)[-1]
            if not re.fullmatch(r"c[a-r]\d\d", base):
                continue
            raw = zf.read(name).decode("latin-1")
            paras = []
            for block in re.split(r"\n\s*\n", raw):
                toks = [t.rsplit("/", 1)[0] for t in block.split() if "/" in t]
                if toks:
                    paras.append(_detokenize(toks))
            out[base] = "\n\n".join(paras)
    return out


def _span(text: str, rng: random.Random, n_words: int) -> str:
    words = text.split()
    i = rng.randrange(0, max(1, len(words) - n_words))
    return " ".join(words[i : i + n_words])


def _perturb(span: str, rng: random.Random, vocab: list[str], frac: float) -> str:
    words = span.split()
    idx = [i for i, w in enumerate(words) if len(w) > 3 and w.isalpha()]
    for i in rng.sample(idx, k=max(1, int(len(words) * frac))):
        words[i] = rng.choice(vocab)
    return " ".join(words)


def _longest_common_run(a: str, b: str) -> int:
    """Longest run of identical consecutive (normalised) words."""
    from app.services.text import tokenize

    wa, wb = [t.word for t in tokenize(a)], [t.word for t in tokenize(b)]
    best = 0
    prev = [0] * (len(wb) + 1)
    for x in wa:
        cur = [0] * (len(wb) + 1)
        for j, y in enumerate(wb, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def build_benchmark(n_docs: int = 60, seed: int = 13):
    rng = random.Random(seed)
    files = _brown_files()
    names = sorted(files)
    rng.shuffle(names)
    source_names, held_out = names[:40], names[40:]
    # Disjoint pools: document-side text never appears in any source.
    doc_pool, filler_pool = held_out[: len(held_out) // 2], held_out[len(held_out) // 2 :]
    vocab = sorted({w for n in doc_pool for w in files[n].split() if w.isalpha() and len(w) > 4})
    mrpc = load_mrpc("test")
    pos = [r for r in mrpc if r[0] == 1 and word_count(r[1]) >= 12]
    neg = [r for r in mrpc if r[0] == 0 and word_count(r[1]) >= 12]
    rng.shuffle(pos)
    rng.shuffle(neg)

    sources = [SourceDoc(f"brown-{n}", f"Brown {n}", files[n], "academic", "benchmark") for n in source_names]
    # MRPC "source" documents: second sentences of pairs, mixed with filler.
    mrpc_src_sents = []
    docs = []
    for d in range(n_docs):
        parts: list[tuple[str, str | None, str | None]] = []  # (text, label, source_id)
        for _ in range(2):
            parts.append((_span(files[rng.choice(doc_pool)], rng, rng.randint(60, 110)), None, None))  # original / same-genre
        k = d % 5
        src_name = rng.choice(source_names)
        if k in (0, 3):
            parts.append((_span(files[src_name], rng, rng.randint(25, 70)), "exact", f"brown-{src_name}"))
        if k in (1, 3):
            parts.append((_perturb(_span(files[src_name], rng, rng.randint(30, 70)), rng, vocab, 0.08), "near_exact", f"brown-{src_name}"))
        if k in (2, 4):
            a = pos.pop()[1:]
            parts.append((a[0] + " " + _span(files[rng.choice(doc_pool)], rng, 25) + ".", "paraphrase", _longest_common_run(a[0], a[1])))
            mrpc_src_sents.append(a[1])
        if k == 4:
            q = _span(files[src_name], rng, 22)
            parts.append((f'As one author wrote, "{q}" (Author, 1961).', "quoted_cited", f"brown-{src_name}"))
        n = neg.pop()
        parts.append((n[1] + " " + _span(files[rng.choice(doc_pool)], rng, 20) + ".", "topical_negative", _longest_common_run(n[1], n[2])))
        mrpc_src_sents.append(n[2])
        # Multi-sentence cases: three consecutive pairs, sources kept in order.
        if d % 2 == 0 and len(pos) >= 3:
            trio = [pos.pop() for _ in range(3)]
            parts.append((" ".join(t[1] for t in trio), "paraphrase_paragraph", max(_longest_common_run(t[1], t[2]) for t in trio)))
            mrpc_src_sents.append(" ".join(t[2] for t in trio))
        elif len(neg) >= 3:
            trio = [neg.pop() for _ in range(3)]
            run = max(_longest_common_run(t[1], t[2]) for t in trio)
            parts.append((" ".join(t[1] for t in trio), "topical_paragraph_negative", run))
            mrpc_src_sents.append(" ".join(t[2] for t in trio))
        rng.shuffle(parts)
        docs.append(parts)
    # Pack the MRPC source sentences into a few documents with filler around them.
    mrpc_docs = []
    chunk = 15
    for i in range(0, len(mrpc_src_sents), chunk):
        body = []
        for s in mrpc_src_sents[i : i + chunk]:
            body.append(s)
            body.append(_span(files[rng.choice(filler_pool)], rng, 30) + ".")
        mrpc_docs.append(SourceDoc(f"mrpc-{i // chunk}", f"MRPC wire {i // chunk}", " ".join(body), "web", "benchmark"))
    return docs, sources + mrpc_docs


def run_benchmark(providers_to_run: dict[str, EmbeddingProvider] | None = None) -> dict:
    docs, sources = build_benchmark()
    out: dict = {"documents": len(docs), "sources": len(sources), "source_words": sum(word_count(s.text) for s in sources)}
    for name, prov in (providers_to_run or providers()).items():
        stats = {lab: {"n": 0, "detected": 0} for lab in ("exact", "near_exact", "paraphrase", "paraphrase_paragraph", "quoted_cited")}
        strength = {"strong": {"tp": 0, "fp": 0}, "moderate": {"tp": 0, "fp": 0}}
        para_neg = {"n": 0, "semantic_strong_flag": 0, "semantic_any_flag": 0}
        neg = {"original": [0, 0], "topical_negative": [0, 0]}  # [passages, falsely flagged]
        topical = {"n": 0, "semantic_flag": 0, "exact_flag": 0, "exact_flag_with_true_verbatim_run": 0, "verbatim_runs_present": 0}
        ious, index_err, index_err_clean, times, false_counted = [], [], [], [], 0
        visible = {"tp": 0, "fp_original": 0, "fp_topical": 0}
        for di, parts in enumerate(docs):
            if di % 10 == 0:
                print(f"[{name}] document {di}/{len(docs)}", file=sys.stderr, flush=True)
            text = normalize_document("\n\n".join(p[0] for p in parts))
            offsets, pos = [], 0
            for p in parts:
                start = text.index(normalize_document(p[0])[:40], pos)
                end = start + len(normalize_document(p[0]))
                offsets.append((start, end))
                pos = end
            sents = all_sentences(segment(text))
            ctx = analyze_citations(text, sents)
            t0 = time.perf_counter()
            r = analyze_similarity(text, sents, ctx, sources, prov)
            times.append(time.perf_counter() - t0)
            findings = r["findings"]
            true_counted = 0
            has_verbatim_negative = False
            for (s, e), (_, label, extra) in zip(offsets, parts, strict=True):
                hits = [f for f in findings if f["start"] < e and f["end"] > s]
                if label in ("paraphrase", "paraphrase_paragraph"):
                    has_verbatim_negative |= (extra or 0) >= 8  # human "paraphrase" that kept 8+ identical words
                if label in stats:
                    stats[label]["n"] += 1
                    want = {"exact": ("exact", "near_exact"), "near_exact": ("exact", "near_exact"), "paraphrase": ("semantic",), "paraphrase_paragraph": ("semantic",), "quoted_cited": ("exact", "near_exact")}[label]
                    for f in hits:
                        if f["method"] == "semantic" and label in ("paraphrase", "paraphrase_paragraph"):
                            strength[f["evidence"]["strength"]]["tp"] += 1
                    good = [f for f in hits if f["method"] in want]
                    if label == "quoted_cited":
                        good = [f for f in good if f["category"] == "quoted_cited"]
                    if any(f["method"] in want and not f.get("weak") for f in hits) and label != "quoted_cited":
                        visible["tp"] += 1
                    if good:
                        stats[label]["detected"] += 1
                        if label in ("exact", "near_exact"):
                            f = max(good, key=lambda f: min(f["end"], e) - max(f["start"], s))
                            inter = max(0, min(f["end"], e) - max(f["start"], s))
                            ious.append(inter / (max(f["end"], e) - min(f["start"], s)))
                    if label in ("exact", "near_exact"):
                        true_counted += word_count(text[s:e])
                elif label == "topical_paragraph_negative":
                    has_verbatim_negative |= (extra or 0) >= 8
                    sem = [f for f in hits if f["method"] == "semantic"]
                    visible["fp_topical"] += any(not f.get("weak") for f in sem)
                    para_neg["n"] += 1
                    para_neg["semantic_any_flag"] += bool(sem)
                    para_neg["semantic_strong_flag"] += any(f["evidence"]["strength"] == "strong" for f in sem)
                    for f in sem:
                        strength[f["evidence"]["strength"]]["fp"] += 1
                elif label == "topical_negative":
                    run = extra or 0
                    has_verbatim_negative |= run >= 8
                    visible["fp_topical"] += any(f["method"] == "semantic" and not f.get("weak") for f in hits) or (
                        run < 8 and any(f["method"] != "semantic" for f in hits)
                    )
                    topical["n"] += 1
                    true_run = run >= 8
                    topical["verbatim_runs_present"] += true_run
                    sem = [f for f in hits if f["method"] == "semantic"]
                    exa = [f for f in hits if f["method"] != "semantic"]
                    for f in sem:
                        strength[f["evidence"]["strength"]]["fp"] += 1
                    topical["semantic_flag"] += bool(sem)
                    topical["exact_flag"] += bool(exa)
                    topical["exact_flag_with_true_verbatim_run"] += bool(exa) and true_run
                    if sem or (exa and not true_run):
                        neg["topical_negative"][1] += 1
                        false_counted += sum(1 for f in exa if f["counts_toward_index"]) if not true_run else 0
                    neg["topical_negative"][0] += 1
                else:
                    for f in hits:
                        if f["method"] == "semantic":
                            strength[f["evidence"]["strength"]]["fp"] += 1
                    neg["original"][0] += 1
                    visible["fp_original"] += any(not f.get("weak") for f in hits)
                    if hits:
                        neg["original"][1] += 1
                        false_counted += sum(1 for f in hits if f["counts_toward_index"])
            truth_pct = 100 * true_counted / max(1, r["scores"]["countable_words"])
            index_err.append(abs(r["scores"]["similarity_index"] - truth_pct))
            if not has_verbatim_negative:
                index_err_clean.append(abs(r["scores"]["similarity_index"] - truth_pct))
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux reports KiB
        tp = sum(v["detected"] for k, v in stats.items() if k != "quoted_cited")
        positives = sum(v["n"] for k, v in stats.items() if k != "quoted_cited")
        fp = neg["original"][1] + neg["topical_negative"][1]
        out[name] = {
            "recall_by_type": {k: round(v["detected"] / v["n"], 3) if v["n"] else None for k, v in stats.items()},
            "counts_by_type": {k: v["n"] for k, v in stats.items()},
            "passage_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "passage_recall": round(tp / positives, 3) if positives else None,
            "false_positive_rate_original_text": round(neg["original"][1] / max(1, neg["original"][0]), 3),
            "false_positive_rate_topical_non_paraphrase": round(neg["topical_negative"][1] / max(1, neg["topical_negative"][0]), 3),
            "topical_non_paraphrase_detail": topical,
            "multi_sentence_negative_detail": para_neg,
            "semantic_flags_by_strength": {
                k: {**v, "precision": round(v["tp"] / (v["tp"] + v["fp"]), 3) if v["tp"] + v["fp"] else None} for k, v in strength.items()
            },
            "false_flags_counted_in_index": false_counted,
            "exact_span_iou_mean": round(float(np.mean(ious)), 3) if ious else None,
            "similarity_index_mean_abs_error_pct_points": round(float(np.mean(index_err)), 2),
            "similarity_index_mae_docs_without_mrpc_verbatim_runs": {"mae": round(float(np.mean(index_err_clean)), 2) if index_err_clean else None, "docs": len(index_err_clean)},
            "default_view_weak_signals_hidden": {
                "passage_precision": round(visible["tp"] / (visible["tp"] + visible["fp_original"] + visible["fp_topical"]), 3) if visible["tp"] else None,
                "false_positive_rate_original_text": round(visible["fp_original"] / max(1, neg["original"][0]), 3),
                "false_positive_rate_topical_negatives": round(visible["fp_topical"] / max(1, neg["topical_negative"][0] + para_neg["n"]), 3),
            },
            "seconds_per_document_mean": round(float(np.mean(times)), 3),
            "process_peak_rss_mb": round(peak / 1e6, 1),
        }
    RESULTS.mkdir(exist_ok=True)
    target = RESULTS / "similarity_benchmark.json"
    if providers_to_run is not None and target.exists():
        merged = json.loads(target.read_text())
        merged.update(out)  # re-ran a subset of providers
        out = merged
    target.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--providers", help="comma-separated subset, e.g. lexical,wordvec")
    a = ap.parse_args()
    if a.calibrate or a.all:
        print(json.dumps(calibrate(), indent=2))
    if a.benchmark or a.all:
        chosen = None
        if a.providers:
            avail = providers()
            chosen = {k: avail[k] for k in a.providers.split(",") if k in avail}
        print(json.dumps(run_benchmark(chosen), indent=2))
