import { memo, useMemo } from "react";
import type { Finding } from "../lib/api";
import { GROUP_META, groupOf, type FindingGroup } from "../lib/format";
import { cx } from "./ui";

// When findings overlap, the most important one is drawn.
const PRIORITY: FindingGroup[] = ["exact", "citation", "paraphrase", "ai", "ok", "quality"];

type Segment = { start: number; end: number; top: Finding | null; all: Finding[]; endsFinding: Finding | null };

function buildSegments(text: string, findings: Finding[]): Segment[] {
  const bounds = new Set<number>([0, text.length]);
  for (const f of findings) {
    bounds.add(Math.max(0, Math.min(text.length, f.start)));
    bounds.add(Math.max(0, Math.min(text.length, f.end)));
  }
  const points = [...bounds].sort((a, b) => a - b);
  const sorted = [...findings].sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let active: Finding[] = [];
  let next = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    while (next < sorted.length && sorted[next].start <= a) active.push(sorted[next++]);
    active = active.filter((f) => f.end > a);
    const covering = active.filter((f) => f.start <= a && f.end >= b);
    let top: Finding | null = null;
    for (const g of PRIORITY) {
      top = covering.find((f) => groupOf(f) === g) ?? null;
      if (top) break;
    }
    const endsFinding = covering.find((f) => f.kind === "similarity" && f.end === b) ?? null;
    segments.push({ start: a, end: b, top, all: covering, endsFinding });
  }
  return segments;
}

type Props = {
  text: string;
  findings: Finding[];
  selectedId: string | null;
  onSelect: (f: Finding) => void;
  sourceRank: Record<string, number>;
};

export const DocumentViewer = memo(function DocumentViewer({ text, findings, selectedId, onSelect, sourceRank }: Props) {
  const segments = useMemo(() => buildSegments(text, findings), [text, findings]);
  const firstSegmentOf = useMemo(() => {
    const seen = new Set<string>();
    const map = new Map<number, string>();
    for (const s of segments) {
      if (s.top && !seen.has(s.top.id)) {
        seen.add(s.top.id);
        map.set(s.start, s.top.id);
      }
    }
    return map;
  }, [segments]);

  return (
    <div className="whitespace-pre-wrap break-words font-serif text-[16.5px] leading-[1.85] text-slate-800 dark:text-slate-200">
      {segments.map((s) => {
        const chunk = text.slice(s.start, s.end);
        if (!s.top) return <span key={s.start}>{chunk}</span>;
        const group = groupOf(s.top);
        const meta = GROUP_META[group];
        const selected = s.all.some((f) => f.id === selectedId);
        const anchorId = firstSegmentOf.get(s.start);
        return (
          <span key={s.start}>
            <mark
              id={anchorId ? `f-${anchorId}` : undefined}
              data-group={group}
              role="button"
              tabIndex={anchorId ? 0 : -1}
              aria-label={`${meta.label}: ${chunk.slice(0, 80)}`}
              onClick={() => onSelect(s.top as Finding)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(s.top as Finding);
                }
              }}
              className={cx(
                "cursor-pointer rounded-[3px] px-[1px] text-inherit underline decoration-2 underline-offset-[5px] transition",
                meta.mark,
                meta.underline,
                selected && "ring-2 ring-brand-500 ring-offset-1 dark:ring-offset-slate-900",
              )}
            >
              {chunk}
            </mark>
            {s.endsFinding && s.endsFinding.kind === "similarity" && sourceRank[s.endsFinding.source_id] !== undefined && (
              <sup className="ml-0.5 select-none rounded bg-slate-800 px-1 font-sans text-[10px] font-bold text-white dark:bg-slate-200 dark:text-slate-900" aria-hidden>
                {sourceRank[s.endsFinding.source_id]}
              </sup>
            )}
          </span>
        );
      })}
    </div>
  );
});
