"""Change tracking shared by both editors."""

import difflib
import re
from dataclasses import asdict, dataclass

_TOKEN_RE = re.compile(r"\s+|[^\W_]+(?:['’][^\W_]+)*|[^\w\s]", re.UNICODE)


@dataclass
class Change:
    id: str
    start: int  # offsets into the ORIGINAL text
    end: int
    original: str
    replacement: str
    reason: str
    kind: str = "rule"  # rule | ai
    blocked: bool = False  # true when applying it would drop protected content
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def apply_changes(text: str, changes: list[Change] | list[dict], accepted: set[str] | None = None) -> str:
    """Apply non-overlapping changes (all, or only the accepted ids)."""
    items = [c if isinstance(c, dict) else c.to_dict() for c in changes]
    items = [c for c in items if (accepted is None and not c.get("blocked")) or (accepted is not None and c["id"] in accepted)]
    items.sort(key=lambda c: c["start"])
    out, pos = [], 0
    for c in items:
        if c["start"] < pos:
            continue  # overlapping change: skip rather than corrupt the text
        out.append(text[pos : c["start"]])
        out.append(c["replacement"])
        pos = c["end"]
    out.append(text[pos:])
    return "".join(out)


def diff_changes(original: str, revised: str, reason: str, kind: str = "ai", prefix: str = "c") -> list[Change]:
    """Word-level diff of two texts, grouped into readable hunks."""
    a = [(m.group(), m.start()) for m in _TOKEN_RE.finditer(original)]
    b = [m.group() for m in _TOKEN_RE.finditer(revised)]
    sm = difflib.SequenceMatcher(a=[t for t, _ in a], b=b, autojunk=False)
    raw: list[tuple[int, int, str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        start = a[i1][1] if i1 < len(a) else len(original)
        end = a[i2 - 1][1] + len(a[i2 - 1][0]) if i2 > i1 else start
        raw.append((start, end, "".join(b[j1:j2])))
    # Merge hunks separated only by whitespace or a single short word.
    merged: list[list] = []
    for start, end, rep in raw:
        if merged:
            ps, pe, prep = merged[-1]
            gap = original[pe:start]
            if len(gap) <= 6 and len(gap.split()) <= 1:
                merged[-1] = [ps, end, prep + gap + rep]
                continue
        merged.append([start, end, rep])
    return [
        Change(id=f"{prefix}{i + 1}", start=s, end=e, original=original[s:e], replacement=r, reason=reason, kind=kind)
        for i, (s, e, r) in enumerate(merged)
        if original[s:e] != r
    ]
