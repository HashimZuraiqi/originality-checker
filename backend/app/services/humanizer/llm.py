"""LLM writing assistant (Anthropic Claude).

The model rewrites text in a requested mode and explains its major edits.
Safety net around it:
* the prompt forbids new facts, changed meaning, removed citations or
  altered quotations, and forbids "detector evasion" goals;
* after the call, every citation, quotation, number and URL of the original
  must still be present; changes that would drop one are marked `blocked`
  and are not applied unless the user explicitly accepts them;
* refusals and API errors are surfaced, never silently replaced.
"""

import json
import logging
import re

import anthropic

from ...config import get_settings
from .changes import Change, apply_changes, diff_changes
from .rules import MODES, protected_spans

log = logging.getLogger(__name__)

MODE_GUIDANCE = {
    "natural": "Make it read naturally, as a thoughtful person would write it: varied sentence length, plain words, no stock phrases or filler. Keep a friendly but competent tone.",
    "academic": "Formal academic register: precise, impersonal where appropriate, cautious claims, discipline-appropriate vocabulary, no contractions or colloquialisms.",
    "professional": "Professional business register: clear, direct, confident, no jargon for its own sake.",
    "simple": "Simple and clear: short sentences, common words, one idea per sentence, suitable for a general reader.",
    "concise": "Concise: remove redundancy and filler, merge repetitive sentences, keep every substantive point.",
    "detailed": "Detailed and explanatory: clarify reasoning and define terms where helpful, using ONLY information already present or logically implied in the text. Never add new facts, data, examples presented as real, or sources.",
}

SYSTEM_PROMPT = """You are an academic writing tutor inside an academic-integrity platform. You improve the clarity, flow and naturalness of a student's or researcher's own writing while preserving their meaning and authorship.

Hard rules:
1. Preserve meaning exactly. Do not add facts, statistics, examples presented as real, or claims that are not in the original.
2. Keep every citation (e.g. "(Smith, 2020)", "[3]") exactly as written and attached to the claim it supports.
3. Keep text inside quotation marks verbatim.
4. Keep numbers, units, names, technical terms and URLs unchanged.
5. Keep the paragraph structure (same number of paragraphs, separated by blank lines).
6. Your goal is better writing, not evading plagiarism or AI detectors. If the text appears copied from a source, improve it but note that rewording copied text still requires citing the source.

Return JSON with:
- "rewritten": the full rewritten text.
- "explanations": up to 6 short, specific explanations of the most important kinds of changes, written to teach the author (e.g. "Split the 52-word opening sentence so the main claim comes first").
- "concerns": anything the author should check (empty list if none)."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "rewritten": {"type": "string"},
        "explanations": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rewritten", "explanations", "concerns"],
    "additionalProperties": False,
}

CHUNK_WORDS = 1200


class AssistantUnavailable(Exception):
    pass


def _client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise AssistantUnavailable("The AI writing assistant is not configured (ANTHROPIC_API_KEY is not set).")
    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0)


def _chunks(text: str) -> list[tuple[int, int]]:
    """Split on paragraph boundaries into pieces of about CHUNK_WORDS words."""
    spans: list[tuple[int, int]] = []
    chunk_start = seg_start = words = 0
    for m in re.finditer(r"\n\s*\n", text):
        words += len(text[seg_start : m.start()].split())
        seg_start = m.end()
        if words >= CHUNK_WORDS:
            spans.append((chunk_start, m.end()))
            chunk_start, words = m.end(), 0
    spans.append((chunk_start, len(text)))
    return [s for s in spans if text[s[0] : s[1]].strip()]


def _call(client: anthropic.Anthropic, text: str, mode: str) -> dict:
    model = get_settings().anthropic_model
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Mode: {MODES[mode]}. {MODE_GUIDANCE[mode]}\n\nText to rewrite:\n<text>\n{text}\n</text>",
            }
        ],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        thinking={"type": "adaptive"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise AssistantUnavailable("The AI assistant declined to rewrite this passage.")
    if response.stop_reason == "max_tokens":
        raise AssistantUnavailable("The passage is too long for one rewrite; select a shorter passage.")
    block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
    if block is None:
        raise AssistantUnavailable("The AI assistant returned no text.")
    data = json.loads(block.text)
    data["model"] = getattr(response, "model", model)
    return data


def _protected_strings(text: str) -> list[str]:
    return sorted({text[a:b] for a, b in protected_spans(text) if b - a >= 2}, key=len, reverse=True)


def llm_rewrite(text: str, mode: str, client: anthropic.Anthropic | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    client = client or _client()
    rewritten_parts, explanations, concerns, model = [], [], [], None
    try:
        for a, b in _chunks(text):
            piece = text[a:b]
            lead = piece[: len(piece) - len(piece.lstrip())]
            trail = piece[len(piece.rstrip()) :]
            data = _call(client, piece.strip(), mode)
            model = data.get("model")
            rewritten_parts.append(lead + data["rewritten"].strip() + trail)
            explanations.extend(data.get("explanations", []))
            concerns.extend(data.get("concerns", []))
    except anthropic.AuthenticationError as e:
        raise AssistantUnavailable("The AI assistant's API key was rejected.") from e
    except anthropic.RateLimitError as e:
        raise AssistantUnavailable("The AI assistant is busy (rate limited). Try again in a minute.") from e
    except anthropic.APIConnectionError as e:
        raise AssistantUnavailable("Could not reach the AI assistant service.") from e
    except anthropic.APIStatusError as e:
        raise AssistantUnavailable(f"The AI assistant returned an error ({e.status_code}).") from e
    except json.JSONDecodeError as e:
        raise AssistantUnavailable("The AI assistant returned an unreadable response.") from e

    revised = "".join(rewritten_parts)
    changes = diff_changes(text, revised, reason="AI assistant edit", kind="ai", prefix="a")
    # Integrity check: block changes that remove protected content.
    for c in changes:
        lost = [p for p in _protected_strings(c.original) if p not in c.replacement]
        if lost:
            c.blocked = True
            c.warning = "Would remove or alter protected content: " + "; ".join(f"“{p[:60]}”" for p in lost[:3])
    missing = [p for p in _protected_strings(text) if p not in revised]
    if missing:
        concerns.append(
            f"{len(missing)} citation(s), quotation(s) or number(s) were changed by the assistant; those edits are blocked by default."
        )
    return {
        "engine": "llm",
        "model": model,
        "text": apply_changes(text, changes),
        "changes": [c.to_dict() for c in changes],
        "explanations": explanations[:12],
        "concerns": concerns[:12],
    }


def assistant_available() -> bool:
    return bool(get_settings().anthropic_api_key)


__all__ = ["llm_rewrite", "assistant_available", "AssistantUnavailable", "Change"]
