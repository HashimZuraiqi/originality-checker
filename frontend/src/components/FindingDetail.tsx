import { Bot, BookMarked, CheckCircle2, ExternalLink, Lightbulb, PenLine, Quote, XCircle } from "lucide-react";
import type { Finding, SourceRow } from "../lib/api";
import { GROUP_META, SEVERITY_LABEL, findingTitle, groupOf } from "../lib/format";
import { Badge } from "./ui";

function sourceLink(s: SourceRow): string | null {
  if (s.url) return s.url;
  if (s.doi) return `https://doi.org/${s.doi}`;
  return null;
}

export function FindingDetail({ finding, text, sources, onOpenTab }: { finding: Finding; text: string; sources: SourceRow[]; onOpenTab: (tab: string) => void }) {
  const group = groupOf(finding);
  const meta = GROUP_META[group];
  const passage = text.slice(finding.start, finding.end);

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <Badge tone={finding.severity === "high" ? "red" : finding.severity === "medium" ? "amber" : "slate"}>Severity: {SEVERITY_LABEL[finding.severity]}</Badge>
      </div>
      <h3 className="text-base font-semibold">{findingTitle(finding)}</h3>

      {finding.kind === "similarity" && (() => {
        const src = sources.find((s) => s.id === finding.source_id);
        const link = src ? sourceLink(src) : null;
        return (
          <>
            <dl className="grid grid-cols-2 gap-2 rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-950/60">
              <div>
                <dt className="text-slate-500">Match type</dt>
                <dd className="font-semibold capitalize">{finding.method.replace("_", "-")}</dd>
              </div>
              <div>
                <dt className="text-slate-500">{finding.method === "semantic" ? "Semantic similarity" : "Word coverage"}</dt>
                <dd className="font-semibold tabular-nums">{finding.method === "semantic" ? finding.score.toFixed(2) : `${Math.round(finding.score * 100)}%`}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Length</dt>
                <dd className="font-semibold">{finding.words} words</dd>
              </div>
              <div>
                <dt className="text-slate-500">Citation nearby</dt>
                <dd className="flex items-center gap-1 font-semibold">
                  {finding.citation_detected ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden /> : <XCircle className="h-3.5 w-3.5 text-red-500" aria-hidden />}
                  {finding.citation_detected ? "Yes" : "No"}
                </dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500">Counts toward similarity index</dt>
                <dd className="font-semibold">{finding.counts_toward_index ? "Yes" : "No (shown for review only)"}</dd>
              </div>
            </dl>

            {src && (
              <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
                <p className="text-xs font-medium text-slate-500">
                  Source {src.rank} · {src.provider} · {src.text_kind === "full_text" ? "full text compared" : `${src.text_kind} only`}
                </p>
                <p className="mt-1 font-semibold leading-snug">{src.title}</p>
                {(src.authors.length > 0 || src.year) && (
                  <p className="mt-0.5 text-xs text-slate-500">
                    {src.authors.slice(0, 3).join(", ")}
                    {src.authors.length > 3 ? " et al." : ""}
                    {src.year ? ` (${src.year})` : ""}
                  </p>
                )}
                {link && (
                  <a href={link} target="_blank" rel="noopener noreferrer" className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400">
                    Open source <ExternalLink className="h-3 w-3" aria-hidden />
                  </a>
                )}
              </div>
            )}

            <div className="grid gap-3">
              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Your text</p>
                <blockquote className="max-h-52 overflow-y-auto rounded-xl bg-red-50/70 p-3 font-serif text-[14px] leading-relaxed dark:bg-red-500/10">{passage}</blockquote>
              </div>
              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Source text</p>
                <blockquote className="max-h-52 overflow-y-auto rounded-xl bg-slate-50 p-3 font-serif text-[14px] leading-relaxed dark:bg-slate-950/60">
                  {finding.source_excerpt || "The matching words are spread across the source, so no single excerpt is shown."}
                </blockquote>
              </div>
            </div>

            {finding.method === "semantic" && finding.evidence.pairs && (
              <details className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-950/60">
                <summary className="cursor-pointer font-semibold">Sentence-level evidence ({finding.evidence.pairs.length})</summary>
                <ul className="mt-2 space-y-2">
                  {finding.evidence.pairs.map((p, i) => (
                    <li key={i} className="border-l-2 border-orange-300 pl-2">
                      <p>
                        <span className="font-semibold">Yours:</span> {p.sentence}
                      </p>
                      <p className="mt-0.5">
                        <span className="font-semibold">Source:</span> {p.source_sentence}
                      </p>
                      <p className="mt-0.5 text-slate-500">
                        cosine {p.cosine.toFixed(2)} · shared content words {Math.round(p.shared_content_words * 100)}%
                        {p.cross_encoder !== undefined && ` · cross-encoder ${p.cross_encoder.toFixed(2)}`}
                      </p>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <div className="rounded-xl bg-brand-50/60 p-3 dark:bg-brand-500/10">
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
                <Quote className="h-3.5 w-3.5" aria-hidden /> Why this was flagged
              </p>
              <p className="mt-1 leading-relaxed">{finding.explanation}</p>
            </div>
            <div className="rounded-xl bg-emerald-50/60 p-3 dark:bg-emerald-500/10">
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
                <Lightbulb className="h-3.5 w-3.5" aria-hidden /> Next steps
              </p>
              <p className="mt-1 leading-relaxed">{finding.next_steps}</p>
            </div>
          </>
        );
      })()}

      {finding.kind === "quality" && (
        <>
          <blockquote className="rounded-xl bg-blue-50/70 p-3 font-serif text-[14px] dark:bg-blue-500/10">{passage}</blockquote>
          <p className="leading-relaxed">{finding.message}</p>
          {finding.replacement !== null && finding.replacement !== undefined && (
            <p>
              Suggested: <span className="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-200">{finding.replacement || "(remove)"}</span>
            </p>
          )}
          {finding.suggestion && (
            <p className="flex gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-950/60">
              <PenLine className="h-4 w-4 shrink-0 text-blue-500" aria-hidden />
              {finding.suggestion}
            </p>
          )}
        </>
      )}

      {finding.kind === "citation" && (
        <>
          <blockquote className="rounded-xl bg-yellow-50 p-3 font-serif text-[14px] dark:bg-yellow-500/10">{passage}</blockquote>
          <p className="flex gap-2 leading-relaxed">
            <BookMarked className="h-4 w-4 shrink-0 text-amber-500" aria-hidden />
            {finding.message}
          </p>
          <button onClick={() => onOpenTab("citations")} className="text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400">
            See all citation checks →
          </button>
        </>
      )}

      {finding.kind === "ai" && (
        <>
          <p className="flex gap-2 leading-relaxed">
            <Bot className="h-4 w-4 shrink-0 text-violet-500" aria-hidden />
            {finding.message}
          </p>
          <p className="rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-950/60 dark:text-slate-300">
            Style indicators can also appear in formal, formulaic or non-native writing. Use them to start a conversation about the writing process, not as evidence on their own.
          </p>
          <button onClick={() => onOpenTab("ai")} className="text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400">
            See how indicators were measured →
          </button>
        </>
      )}
    </div>
  );
}
