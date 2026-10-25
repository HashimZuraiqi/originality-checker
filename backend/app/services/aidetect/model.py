"""Logistic-regression combination of stylometric features.

Why logistic regression and not a neural detector: it is transparent (every
feature's contribution to the score can be shown to the user), it trains on
small data without overfitting badly, and its output is calibrated by
construction on the training distribution (Platt-style). Its weaknesses are
documented in docs/EVALUATION.md.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .features import FEATURE_NAMES

WEIGHTS_FILE = Path(__file__).with_name("weights.json")

# Features whose distribution is skewed are log-transformed before scaling.
LOG_FEATURES = {"marker_rate", "contraction_rate", "emdash_rate", "oov_rate", "repetition", "transition_opener_rate", "mtld"}


def transform(features: dict[str, float]) -> np.ndarray:
    row = []
    for name in FEATURE_NAMES:
        v = float(features.get(name, 0.0))
        row.append(math.log1p(max(v, 0.0)) if name in LOG_FEATURES else v)
    return np.array(row, dtype=np.float64)


@dataclass
class LogisticModel:
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: float
    meta: dict

    def logit(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mean) / self.std
        return z @ self.coef + self.intercept

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logit(x)))

    def contributions(self, x: np.ndarray) -> dict[str, float]:
        z = (x - self.mean) / self.std
        return {name: float(c) for name, c in zip(FEATURE_NAMES, z * self.coef, strict=True)}

    def to_json(self) -> dict:
        return {
            "features": FEATURE_NAMES,
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "coef": self.coef.tolist(),
            "intercept": self.intercept,
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, data: dict) -> "LogisticModel":
        if data["features"] != FEATURE_NAMES:
            raise ValueError("weights.json was trained on a different feature set; retrain with eval/ai_detection.py")
        return cls(np.array(data["mean"]), np.array(data["std"]), np.array(data["coef"]), float(data["intercept"]), data.get("meta", {}))


# Direction each feature is expected to push (+1 toward "AI-like"), from the
# literature cited in features.py. 0 = no prior expectation.
EXPECTED_SIGN = {
    "burstiness": -1,
    "mean_sentence_length": 0,
    "mtld": 0,
    "commonness": 1,
    "marker_rate": 1,
    "transition_opener_rate": 1,
    "paragraph_uniformity": 1,
    "repetition": 0,
    "contraction_rate": -1,
    "oov_rate": -1,
    "punctuation_variety": -1,
    "emdash_rate": 1,
}


def fit_constrained(X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, list[str]]:
    """Fit, then drop features whose learned direction contradicts the
    published expectation and refit, until no violations remain. This keeps
    the model from encoding quirks of the training corpora (e.g. a genre
    that happens to use more parentheses) as "AI evidence"."""
    active = np.ones(X.shape[1], dtype=bool)
    dropped: list[str] = []
    while True:
        mean, std, coef_a, b = fit_logistic(X[:, active], y, l2)
        coef = np.zeros(X.shape[1])
        coef[active] = coef_a
        violations = [
            i for i, name in enumerate(FEATURE_NAMES)
            if active[i] and EXPECTED_SIGN[name] != 0 and np.sign(coef[i]) == -EXPECTED_SIGN[name] and abs(coef[i]) > 1e-6
        ]
        if not violations:
            full_mean = X.mean(axis=0)
            full_std = X.std(axis=0)
            full_std[full_std == 0] = 1.0
            full_mean[active], full_std[active] = mean, std
            return full_mean, full_std, coef, b, dropped
        worst = max(violations, key=lambda i: abs(coef[i]))
        active[worst] = False
        dropped.append(FEATURE_NAMES[worst])


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 100) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Newton/IRLS with L2 penalty and balanced class weights."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    Z = (X - mean) / std
    n, d = Z.shape
    pos = y.sum()
    w_pos, w_neg = n / (2 * max(pos, 1)), n / (2 * max(n - pos, 1))
    sw = np.where(y == 1, w_pos, w_neg)
    A = np.hstack([Z, np.ones((n, 1))])
    beta = np.zeros(d + 1)
    reg = np.full(d + 1, l2)
    reg[-1] = 0.0  # no penalty on the intercept
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(A @ beta)))
        g = A.T @ (sw * (p - y)) + reg * beta
        H = (A * (sw * p * (1 - p))[:, None]).T @ A + np.diag(reg)
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return mean, std, beta[:-1], float(beta[-1])


_model: LogisticModel | None = None


def load_model() -> LogisticModel | None:
    global _model
    if _model is None and WEIGHTS_FILE.exists():
        _model = LogisticModel.from_json(json.loads(WEIGHTS_FILE.read_text()))
    return _model


def reset_model_cache() -> None:
    global _model
    _model = None
