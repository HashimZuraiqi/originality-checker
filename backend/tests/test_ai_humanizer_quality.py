import json
from types import SimpleNamespace

import pytest

from app.services.aidetect.detector import analyze_ai_writing
from app.services.aidetect.features import extract_features, mtld
from app.services.citations import analyze_citations
from app.services.humanizer import llm
from app.services.humanizer.changes import apply_changes, diff_changes
from app.services.humanizer.rules import rule_based_rewrite
from app.services.quality import analyze_quality
from app.services.text import all_sentences, segment

AI_TEXT = open(__file__.replace("test_ai_humanizer_quality.py", "../eval/data/ai_samples.txt")).read().split("### ai-essay-03 | essay")[1].split("###")[0].strip()
HUMAN_TEXT = (
    "I got to the station late, of course. The 7:42 had already gone, and the next one wasn't for forty minutes, so I sat on the "
    "cold bench and watched pigeons fight over a crust. My phone was dead. Someone had scratched 'MARCO WAS HERE 98' into the armrest "
    "and I wondered, idly, whether Marco ever came back to check. A woman with three suitcases asked me if this was the platform for "
    "Leeds; I said I hoped so, since it was mine too. We ended up talking about her brother's farm (sheep, mostly, and one "
    "bad-tempered llama) until the train finally crawled in. I never got her name. Sometimes those are the conversations you remember "
    "longest, aren't they? Anyway: I missed the meeting, but I don't regret the wait. My boss was furious, obviously. "
    "She sent three emails before lunch, each shorter than the last; the final one just said 'Call me.' I didn't, not straight away. "
    "I bought a coffee that tasted of burnt toast and read the free paper twice."
)


def test_mtld_and_features_are_finite():
    f = extract_features(HUMAN_TEXT, segment(HUMAN_TEXT))
    assert set(f) >= {"burstiness", "marker_rate", "mtld"}
    assert mtld("the the the the the the the the the the the".split()) < mtld(HUMAN_TEXT.lower().split())


def test_short_text_is_insufficient():
    r = analyze_ai_writing("Too short to judge. Only a few words here.", segment("x"), "en")
    assert r["verdict"] == "insufficient_text"


def test_non_english_not_scored():
    r = analyze_ai_writing(HUMAN_TEXT, segment(HUMAN_TEXT), "fr")
    assert r["verdict"] == "insufficient_text" and r["language_supported"] is False


def test_ai_essay_vs_human_narrative_ordering():
    ai = analyze_ai_writing(AI_TEXT, segment(AI_TEXT), "en")
    human = analyze_ai_writing(HUMAN_TEXT, segment(HUMAN_TEXT), "en")
    assert ai["score"] > human["score"]
    assert human["verdict"] != "many_indicators"
    assert ai["caveats"] and ai["features"][0]["contribution"] != 0
    assert "probability" in ai["score_note"]


def test_rules_preserve_protected_spans():
    text = 'It is important to note that 45% of firms utilize AI (Smith, 2020). He said "in order to win, utilize everything" loudly.'
    r = rule_based_rewrite(text, "concise")
    assert "(Smith, 2020)" in r.text and '"in order to win, utilize everything"' in r.text and "45%" in r.text
    assert r.text.startswith("45% of firms use AI")
    assert apply_changes(text, r.changes) == r.text


def test_detailed_mode_requires_llm():
    r = rule_based_rewrite("Some text that could be explained in more depth by a person.", "detailed")
    assert r.changes == [] and "AI writing assistant" in r.notes[0]


def test_diff_changes_roundtrip():
    a = "The cat sat on the mat. It was happy."
    b = "The cat sat on a rug. It was very happy."
    changes = diff_changes(a, b, "edit")
    assert apply_changes(a, changes) == b
    assert apply_changes(a, changes, accepted=set()) == a


class FakeAnthropic:
    """Stands in for anthropic.Anthropic; returns a structured-output message."""

    def __init__(self, rewritten, stop_reason="end_turn"):
        self.rewritten = rewritten
        self.stop_reason = stop_reason
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        body = json.dumps({"rewritten": self.rewritten, "explanations": ["Shortened the opening."], "concerns": []})
        return SimpleNamespace(stop_reason=self.stop_reason, model=kwargs["model"], content=[SimpleNamespace(type="text", text=body)])


def test_llm_rewrite_blocks_changes_that_drop_citations(monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "anthropic_api_key", "test-key")
    original = "In order to succeed, firms adopt AI quickly (Smith, 2020). The results are good."
    fake = FakeAnthropic("Firms adopt AI quickly to succeed. The results are good.")
    out = llm.llm_rewrite(original, "concise", client=fake)
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    blocked = [c for c in out["changes"] if c["blocked"]]
    assert blocked and "(Smith, 2020)" in blocked[0]["warning"]
    assert "(Smith, 2020)" in out["text"]  # blocked edits are not applied by default
    assert out["concerns"]


def test_llm_refusal_surfaces(monkeypatch):
    fake = FakeAnthropic("", stop_reason="refusal")
    with pytest.raises(llm.AssistantUnavailable, match="declined"):
        llm.llm_rewrite("A perfectly normal sentence to improve here.", "natural", client=fake)


def test_quality_checks_and_transparent_score():
    text = "This is a example. The the results was analyzed in order to determine outcomes. she could of tried."
    paras = segment(text)
    sents = all_sentences(paras)
    q = analyze_quality(text, paras, sents, analyze_citations(text, sents), "en")
    cats = {f["category"] for f in q["findings"]}
    assert {"article", "repeated_word", "wordiness", "word_choice", "capitalization", "passive_voice"} <= cats
    assert set(q["subscores"]) == set(q["formulas"]) == {"mechanics", "clarity", "concision", "readability"}
    assert q["score"] == pytest.approx(sum(q["subscores"].values()) / 4, abs=0.05)
