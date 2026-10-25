import numpy as np
import pytest

from app.services.citations import analyze_citations
from app.services.similarity.embeddings import EmbeddingProvider, LexicalProvider, content_stems
from app.services.similarity.engine import analyze_similarity
from app.services.similarity.minhash import estimate_jaccard, near_duplicate_groups, shingle_set, signature
from app.services.similarity.types import SourceDoc
from app.services.text import all_sentences, normalize_document, segment

from .fakeweb import PHOTOSYNTHESIS, REVOLUTION


def run(doc: str, sources: list[SourceDoc], provider: EmbeddingProvider | None = None, **opts):
    doc = normalize_document(doc)
    sents = all_sentences(segment(doc))
    ctx = analyze_citations(doc, sents)
    return doc, analyze_similarity(doc, sents, ctx, sources, provider, options=opts or None)


SRC = SourceDoc("s1", "Photosynthesis", PHOTOSYNTHESIS, "encyclopedia", "test")
SRC2 = SourceDoc("s2", "French Revolution", REVOLUTION, "encyclopedia", "test")
ORIGINAL = (
    "Last summer my grandmother taught me how to bake sourdough bread in her tiny kitchen. "
    "We measured flour by the handful and argued about how long the dough should rest before shaping."
)


def test_original_text_has_no_findings():
    _, r = run(ORIGINAL, [SRC, SRC2])
    assert r["scores"]["similarity_index"] == 0
    assert r["findings"] == []


def test_verbatim_copy_is_exact_unattributed_and_fully_counted():
    doc, r = run(PHOTOSYNTHESIS, [SRC])
    assert r["scores"]["similarity_index"] == 100.0
    assert r["scores"]["unattributed_pct"] == 100.0
    f = r["findings"][0]
    assert f["category"] == "unattributed" and f["method"] == "exact" and f["severity"] == "high"
    assert f["source_excerpt"].startswith("Photosynthesis is the process")


def test_case_and_punctuation_changes_still_match_with_offsets():
    copied = PHOTOSYNTHESIS.split(". ")[1].upper().replace(",", "")
    doc, r = run(ORIGINAL + " " + copied + ".", [SRC])
    f = r["findings"][0]
    assert doc[f["start"] : f["end"]].startswith("DURING PHOTOSYNTHESIS")
    assert 0 < r["scores"]["similarity_index"] < 100


def test_near_exact_with_substituted_words_is_one_passage():
    sent = PHOTOSYNTHESIS.split(". ")[1]
    edited = sent.replace("captured", "trapped").replace("minerals", "salts")
    doc, r = run(ORIGINAL + " " + edited + ".", [SRC])
    exact = [f for f in r["findings"] if f["method"] in ("exact", "near_exact")]
    assert len(exact) == 1
    assert exact[0]["method"] == "near_exact"
    assert exact[0]["score"] < 0.97


def test_short_common_phrase_is_ignored():
    _, r = run("I think it would be impossible to say anything else about my weekend at the beach with friends.", [SRC])
    assert r["findings"] == []


def test_quoted_and_cited_is_shown_but_not_counted():
    quote = "it would be impossible to overestimate the importance of photosynthesis in the maintenance of life on Earth"
    doc = f'{ORIGINAL} As the encyclopedia states, "{quote}" (Britannica, 2020). {ORIGINAL}'
    _, r = run(doc, [SRC])
    f = r["findings"][0]
    assert f["category"] == "quoted_cited" and not f["counts_toward_index"]
    assert r["scores"]["similarity_index"] == 0
    assert r["scores"]["quoted_pct"] > 0
    _, r2 = run(doc, [SRC], exclude_quotes=False)
    assert r2["scores"]["similarity_index"] > 0


def test_quoted_without_citation_counts_as_citation_concern():
    quote = "it would be impossible to overestimate the importance of photosynthesis in the maintenance of life on Earth"
    _, r = run(f'{ORIGINAL} Someone once said "{quote}" and I agree. {ORIGINAL}', [SRC])
    assert r["findings"][0]["category"] == "quoted_uncited"
    assert r["scores"]["similarity_index"] > 0


def test_cited_but_unquoted_copy_is_patchwriting():
    sent = PHOTOSYNTHESIS.split(". ")[1]
    _, r = run(f"{ORIGINAL} {sent} (Britannica, 2020).", [SRC])
    assert r["findings"][0]["category"] == "cited_no_quotes"


def test_bibliography_overlap_not_counted():
    body = ORIGINAL * 3
    doc = body + "\n\nReferences\n\n" + "Britannica. (2020). " + PHOTOSYNTHESIS.split(". ")[1] + "."
    _, r = run(doc, [SRC])
    cats = {f["category"] for f in r["findings"]}
    assert cats == {"bibliography"}
    assert r["scores"]["similarity_index"] == 0


def test_each_word_attributed_to_one_source_and_percentages_add_up():
    dup = SourceDoc("s3", "Mirror of photosynthesis", PHOTOSYNTHESIS, "web", "test")
    doc = PHOTOSYNTHESIS + "\n\n" + REVOLUTION
    _, r = run(doc, [SRC, dup, SRC2])
    total = sum(s["percent"] for s in r["sources"])
    assert abs(total - r["scores"]["similarity_index"]) <= 0.3
    assert {s["id"] for s in r["sources"] if s["words"] > 0} == {"s1", "s2"}  # mirror gets nothing


class KeywordProvider(EmbeddingProvider):
    """Deterministic fake: sentences sharing the topic word map close together."""

    name = "fake"
    label = "fake"
    paraphrase_threshold = 0.8
    strong_threshold = 0.95

    def encode(self, texts):
        out = []
        for t in texts:
            stems = set(content_stems(t))
            v = np.array([len(stems & {"revolution", "france", "french", "estat", "general", "brumair", "coup", "1789"}) + 0.01, len(stems & {"democracy", "liberal", "idea", "principl", "valu", "institution"}) + 0.01, 0.2])
            out.append(v / np.linalg.norm(v))
        return np.array(out)


def test_semantic_paraphrase_detected_and_cited_paraphrase_labelled():
    para = (
        "The revolution in France was a time of social and political upheaval which started with the Estates General in 1789 "
        "and finished with the Brumaire coup in 1799. A lot of its ideas are seen as basic principles of liberal democracy, and "
        "its values and institutions are still central to French political debate today."
    )
    _, r = run(ORIGINAL + "\n\n" + para, [SRC2], provider=KeywordProvider())
    sem = [f for f in r["findings"] if f["method"] == "semantic"]
    assert sem and sem[0]["category"] == "paraphrase"
    assert sem[0]["evidence"]["aligned_sentences"] == 2 and sem[0]["evidence"]["strength"] == "strong"
    assert not sem[0]["counts_toward_index"]
    assert r["scores"]["semantic_pct"] > 0 and r["scores"]["similarity_index"] == 0

    _, r2 = run(ORIGINAL + "\n\n" + para.replace("1799.", "1799 (Dupont, 2015)."), [SRC2], provider=KeywordProvider())
    assert any(f["category"] == "paraphrase_cited" for f in r2["findings"])


def test_lexical_provider_is_marked_degraded():
    _, r = run(ORIGINAL, [SRC], provider=LexicalProvider())
    assert r["method"]["semantic_provider"]["degraded"] is True


@pytest.mark.parametrize("edit_fraction", [0.0, 0.02])
def test_minhash_finds_near_duplicates(edit_fraction):
    a = PHOTOSYNTHESIS * 3
    words = a.split()
    for i in range(0, len(words), max(1, int(1 / edit_fraction)) if edit_fraction else len(words) + 1):
        words[i] = "X"
    b = " ".join(words)
    sa, sb, sc = signature(shingle_set(a)), signature(shingle_set(b)), signature(shingle_set(REVOLUTION * 3))
    assert estimate_jaccard(sa, sc) < 0.1
    groups = near_duplicate_groups([sa, sb, sc], threshold=0.6)
    assert [0, 1] in [sorted(g) for g in groups]
