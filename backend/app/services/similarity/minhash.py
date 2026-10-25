"""MinHash signatures + LSH banding (Broder 1997; Indyk & Motwani 1998).

Used to collapse near-duplicate retrieved sources (the same article on a
publisher site, a repository and a mirror), so one passage is not credited
to several copies of the same text.
"""

import numpy as np

from ..text import tokenize

_PRIME = (1 << 61) - 1
_rng = np.random.default_rng(20240607)  # fixed seed: signatures comparable across runs
NUM_PERM = 64
BANDS, ROWS = 16, 4  # threshold ≈ (1/16)^(1/4) ≈ 0.5 Jaccard
_A = _rng.integers(1, _PRIME, NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, _PRIME, NUM_PERM, dtype=np.uint64)


def shingle_set(text: str, k: int = 5, limit_tokens: int = 20000) -> np.ndarray:
    words = [t.word for t in tokenize(text)[:limit_tokens]]
    if len(words) < k:
        return np.array([], dtype=np.uint64)
    hashes = {hash(tuple(words[i : i + k])) & 0xFFFFFFFFFFFF for i in range(len(words) - k + 1)}
    return np.fromiter(hashes, dtype=np.uint64, count=len(hashes))


def signature(shingles: np.ndarray) -> np.ndarray:
    if shingles.size == 0:
        return np.full(NUM_PERM, np.iinfo(np.uint64).max, dtype=np.uint64)
    # (a*x + b) mod p using Python ints would be slow; values are < 2^48 and
    # a < 2^61, so compute in float-free uint64 with wraparound then mod p.
    # Wraparound breaks the exact universal-hash guarantee slightly but keeps
    # permutations independent enough for deduplication.
    vals = (shingles[None, :] * _A[:, None] + _B[:, None]) % np.uint64(_PRIME)
    return vals.min(axis=1)


def estimate_jaccard(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(a == b))


def near_duplicate_groups(signatures: list[np.ndarray], threshold: float = 0.8) -> list[list[int]]:
    """Group indexes whose estimated Jaccard similarity >= threshold, using
    LSH banding to avoid comparing every pair."""
    buckets: dict[tuple, list[int]] = {}
    candidates: set[tuple[int, int]] = set()
    for i, sig in enumerate(signatures):
        for band in range(BANDS):
            key = (band, sig[band * ROWS : (band + 1) * ROWS].tobytes())
            for j in buckets.setdefault(key, []):
                candidates.add((j, i))
            buckets[key].append(i)
    parent = list(range(len(signatures)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in candidates:
        if estimate_jaccard(signatures[i], signatures[j]) >= threshold:
            parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(signatures)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())
