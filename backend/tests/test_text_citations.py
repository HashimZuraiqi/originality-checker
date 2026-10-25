from app.services.citations import analyze_citations, find_bibliography_start
from app.services.text import all_sentences, normalize_document, segment, split_sentences, tokenize


def sentences_of(text):
    return all_sentences(segment(text))


def test_normalization_removes_control_chars_and_collapses_whitespace():
    raw = "Hello world\r\n\r\n\r\n\r\nNext\x07 para   here.­"
    assert normalize_document(raw) == "Hello world\n\nNext para here."


def test_tokenize_keeps_offsets_and_apostrophes():
    text = "It’s 2024: Smith's data."
    toks = tokenize(text)
    assert [t.word for t in toks] == ["it's", "2024", "smith's", "data"]
    assert text[toks[0].start : toks[0].end] == "It’s"


def test_sentence_split_respects_abbreviations_and_decimals():
    text = "Dr. Smith measured 3.5 g (e.g. a sample). Results were clear. J. Doe agreed, et al. also did."
    spans = [text[a:b] for a, b in split_sentences(text)]
    assert spans == ["Dr. Smith measured 3.5 g (e.g. a sample).", "Results were clear.", "J. Doe agreed, et al. also did."]


def test_segment_paragraph_offsets():
    text = "First para one. Second sentence.\n\nSecond para."
    paras = segment(text)
    assert len(paras) == 2
    assert text[paras[1].start : paras[1].end] == "Second para."
    assert [s.paragraph for s in all_sentences(paras)] == [0, 0, 1]


def test_citation_styles_and_linking():
    text = (
        "Warming is accelerating (Smith & Jones, 2020; Lee et al., 2019). Brown (2017) agrees. "
        "Others disagree [1]. One more (Garcia 45).\n\n"
        "References\n\nSmith, J., & Jones, K. (2020). Warming trends. Nature. https://doi.org/10.1000/known.2020\n"
        "Lee, A. (2019). Ocean heat. Science.\nBrown, T. (2017). Visible effects. Journal.\nUnused, Z. (2015). Never cited. Press.\n"
    )
    ctx = analyze_citations(text, sentences_of(text))
    styles = {c.style for c in ctx.citations}
    assert {"author-year", "narrative", "numeric", "mla"} <= styles
    cited = {r.first_author: r.cited for r in ctx.references}
    assert cited == {"Smith": True, "Lee": True, "Brown": True, "Unused": False}
    assert ctx.references[0].doi == "10.1000/known.2020"
    kinds = [i["type"] for i in ctx.issues]
    assert "uncited_reference" in kinds
    assert "missing_reference" in kinds  # [1] and (Garcia 45) have no entries
    assert "mixed_styles" not in kinds  # only one numeric citation


def test_year_mismatch_detected():
    text = "As Brown (2018) argued, the effect is large.\n\n" + "Filler sentence here. " * 10 + "\n\nReferences\n\nBrown, T. (2017). Visible effects. Journal.\n"
    ctx = analyze_citations(text, sentences_of(text))
    assert any(i["type"] == "year_mismatch" for i in ctx.issues)


def test_quote_without_citation_flagged_and_cited_quote_not():
    text = (
        'As Smith (2020) notes, "the evidence for this effect is overwhelming and consistent across many studies" today. '
        'Another claim: "this sentence is quoted but nobody is credited for writing it at all" ends. Plain text follows here. More plain text.'
    )
    ctx = analyze_citations(text, sentences_of(text))
    flagged = [text[i["start"] : i["end"]] for i in ctx.issues if i["type"] == "quote_without_citation"]
    assert len(flagged) == 1 and "nobody is credited" in flagged[0]


def test_bibliography_heading_must_be_late_in_document():
    early = "Sources\n\n" + "Body text sentence. " * 50
    assert find_bibliography_start(early) is None
    late = "Body text sentence. " * 50 + "\n\nWorks Cited\n\nA, B. (2001). T."
    assert find_bibliography_start(late) is not None
