"""Live source-retrieval evaluation (needs internet access).

    python -m eval.retrieval_live --n 20

For each trial: pick a random English Wikipedia article, copy one of its
paragraphs into a document surrounded by unrelated original text, run the
real discovery pipeline (all enabled providers), and check whether the
article was retrieved and the copied paragraph detected.

Reports retrieval success rate, detection rate, similarity index on the
copied passage and search latency. This measures what the bundled offline
tests cannot: whether automatic discovery finds real sources. It could not
be run in the development sandbox (outbound access to these services was
blocked there), so no results are bundled; run it and keep the JSON it
writes to eval/results/.
"""

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.citations import analyze_citations  # noqa: E402
from app.services.discovery.engine import discover_sources  # noqa: E402
from app.services.similarity.embeddings import get_provider  # noqa: E402
from app.services.similarity.engine import analyze_similarity  # noqa: E402
from app.services.text import all_sentences, normalize_document, segment  # noqa: E402

RESULTS = Path(__file__).parent / "results"
FILLER = (
    "In this essay I want to explain why the topic matters to me personally. When I first read about it in school, "
    "I did not expect to spend so much time thinking about it afterwards, but it kept coming back in conversations "
    "with friends and family. The rest of this essay summarises what I learned and what I still find puzzling."
)


def random_paragraphs(client: httpx.Client, n: int, rng: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    while len(out) < n:
        r = client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "generator": "random", "grnnamespace": 0, "grnlimit": 10, "prop": "extracts", "explaintext": 1, "exlimit": 1, "format": "json", "formatversion": 2},
        )
        for page in r.json().get("query", {}).get("pages", []):
            paras = [p for p in (page.get("extract") or "").split("\n") if 60 <= len(p.split()) <= 160]
            if paras:
                out.append((page["title"], rng.choice(paras)))
        time.sleep(1)
    return out[:n]


def main(n: int, seed: int) -> dict:
    settings = get_settings()
    rng = random.Random(seed)
    client = httpx.Client(timeout=30, headers={"User-Agent": settings.user_agent})
    trials = []
    provider = get_provider()
    for title, para in random_paragraphs(client, n, rng):
        doc = normalize_document(f"{FILLER}\n\n{para}\n\n{FILLER}")
        paras = segment(doc)
        sents = all_sentences(paras)
        ctx = analyze_citations(doc, sents)
        t0 = time.perf_counter()
        res = discover_sources(doc, paras, sents, ctx, "en")
        secs = time.perf_counter() - t0
        retrieved = any(title.lower() in s.title.lower() for s in res.sources)
        sim = analyze_similarity(doc, sents, ctx, res.sources, provider)
        start = doc.index(para[:50])
        detected = any(f["start"] < start + len(para) and f["end"] > start and f["method"] != "semantic" for f in sim["findings"])
        trials.append({"article": title, "retrieved": retrieved, "detected": detected, "sources": len(res.sources), "search_seconds": round(secs, 1), "similarity_index": sim["scores"]["similarity_index"]})
        print(trials[-1], flush=True)
    out = {
        "trials": len(trials),
        "retrieval_success_rate": round(sum(t["retrieved"] for t in trials) / len(trials), 3),
        "copied_paragraph_detection_rate": round(sum(t["detected"] for t in trials) / len(trials), 3),
        "median_search_seconds": statistics.median(t["search_seconds"] for t in trials),
        "providers": {p["name"]: p["status"] for p in res.coverage["providers"]},
        "details": trials,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "retrieval_live.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(json.dumps(main(a.n, a.seed), indent=2))
