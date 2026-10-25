import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Filter,
  Info,
  PenLine,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DocumentViewer } from "../components/DocumentViewer";
import { FindingDetail } from "../components/FindingDetail";
import { Badge, Button, Card, EmptyState, Meter, Modal, PageLoader, ScoreRing, Select, Tabs, cx } from "../components/ui";
import { api, download, type DocumentItem, type Finding, type Job, type Report, type Revision } from "../lib/api";
import { useToast } from "../lib/app-state";
import {
  GROUP_META,
  SEVERITY_RANK,
  TONE_TEXT,
  aiTone,
  formatDate,
  groupOf,
  qualityTone,
  similarityBand,
  type FindingGroup,
} from "../lib/format";

type DocDetail = { document: DocumentItem; job: Job | null; reports: { id: string; created_at: string }[]; revisions: Revision[] };
type TabId = "viewer" | "sources" | "ai" | "quality" | "citations" | "coverage";

export default function ReportPage() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [detail, setDetail] = useState<DocDetail | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("viewer");
  const [groups, setGroups] = useState<Record<FindingGroup, boolean>>({ exact: true, paraphrase: true, citation: true, ai: true, ok: false, quality: false });
  const [sourceFilter, setSourceFilter] = useState("");
  const [minSeverity, setMinSeverity] = useState<"all" | "medium">("all");
  const [showWeak, setShowWeak] = useState(false);
  const [selected, setSelected] = useState<Finding | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const d = await api.get<DocDetail>(`/api/documents/${docId}`);
        setDetail(d);
        if (d.document.status !== "completed" && d.job && (d.job.status === "queued" || d.job.status === "running")) {
          navigate(`/analysis/${d.job.id}`, { replace: true });
          return;
        }
        if (d.reports.length) setReport(await api.get<Report>(`/api/reports/${d.reports[0].id}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load the report.");
      }
    })();
  }, [docId, navigate]);

  const allFindings: Finding[] = useMemo(() => {
    if (!report) return [];
    return [...report.similarity.findings, ...report.findings.citation, ...report.findings.ai, ...report.quality.findings];
  }, [report]);

  const counts = useMemo(() => {
    const c: Record<FindingGroup, number> = { exact: 0, paraphrase: 0, citation: 0, ai: 0, ok: 0, quality: 0 };
    for (const f of allFindings) if (!(f.kind === "similarity" && f.weak)) c[groupOf(f)]++;
    return c;
  }, [allFindings]);
  const weakCount = useMemo(() => allFindings.filter((f) => f.kind === "similarity" && f.weak).length, [allFindings]);

  const visible = useMemo(
    () =>
      allFindings
        .filter((f) => groups[groupOf(f)])
        .filter((f) => showWeak || !(f.kind === "similarity" && f.weak))
        .filter((f) => !sourceFilter || (f.kind === "similarity" && f.source_id === sourceFilter))
        .filter((f) => minSeverity === "all" || SEVERITY_RANK[f.severity] <= SEVERITY_RANK.medium)
        .sort((a, b) => a.start - b.start || SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]),
    [allFindings, groups, sourceFilter, minSeverity, showWeak],
  );

  const sourceRank = useMemo(() => Object.fromEntries((report?.similarity.sources ?? []).map((s) => [s.id, s.rank])), [report]);

  const select = useCallback((f: Finding) => {
    setSelected(f);
    requestAnimationFrame(() => document.getElementById(`f-${f.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, []);

  const step = (dir: 1 | -1) => {
    if (!visible.length) return;
    const idx = selected ? visible.findIndex((f) => f.id === selected.id) : -1;
    const next = visible[(idx + dir + visible.length) % visible.length];
    select(next);
  };

  if (error) {
    return <EmptyState icon={<AlertTriangle className="h-6 w-6" />} title={error} action={<Button onClick={() => navigate("/history")}>Back to history</Button>} />;
  }
  if (!detail) return <PageLoader />;
  if (!report) {
    return (
      <EmptyState
        icon={<FileText className="h-6 w-6" />}
        title={detail.job?.status === "failed" ? "The analysis failed" : "No report yet"}
        action={
          <Button
            icon={<RotateCcw className="h-4 w-4" />}
            onClick={async () => {
              const r = await api.post<{ job: Job }>(`/api/documents/${detail.document.id}/analyze`, {});
              navigate(`/analysis/${r.job.id}`);
            }}
          >
            Run analysis
          </Button>
        }
      >
        {detail.job?.error}
      </EmptyState>
    );
  }

  const s = report.summary;
  const band = similarityBand(s.similarity_index);
  const doc = detail.document;

  const doExport = async (format: "pdf" | "docx") => {
    setBusy(format);
    try {
      await download("GET", `/api/reports/${report.id}/export?format=${format}`, undefined, `report.${format}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Export failed.", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <Link to="/history" className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
          <ArrowLeft className="h-4 w-4" aria-hidden /> History
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-bold tracking-tight sm:text-3xl">{doc.title}</h1>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              {doc.author_name && <>{doc.author_name} · </>}
              {report.document.words.toLocaleString()} words · {report.document.language_name} · analysed {formatDate(report.generated_at)}
              {doc.late && <Badge tone="red" className="ml-2">Late</Badge>}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" icon={<Download className="h-4 w-4" />} loading={busy === "pdf"} onClick={() => doExport("pdf")}>
              PDF
            </Button>
            <Button variant="secondary" icon={<Download className="h-4 w-4" />} loading={busy === "docx"} onClick={() => doExport("docx")}>
              Word
            </Button>
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} loading={busy === "rerun"} onClick={async () => {
              setBusy("rerun");
              try {
                const r = await api.post<{ job: Job }>(`/api/documents/${doc.id}/analyze`, {});
                navigate(`/analysis/${r.job.id}`);
              } catch (e) {
                toast(e instanceof Error ? e.message : "Could not start.", "error");
                setBusy(null);
              }
            }}>
              Re-run
            </Button>
            <Button icon={<PenLine className="h-4 w-4" />} onClick={() => navigate(`/studio/${doc.id}`)}>
              Improve writing
            </Button>
            <Button variant="ghost" aria-label="Delete document" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {s.coverage_gap && (
        <div role="alert" className="flex gap-3 rounded-2xl bg-amber-50 p-4 text-sm text-amber-900 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
          <div>
            <p className="font-semibold">Limited coverage</p>
            <p className="mt-0.5">{report.coverage.summary} A low similarity score here does not mean the text is original.</p>
            <button className="mt-1 font-semibold underline" onClick={() => setTab("coverage")}>
              See what was searched
            </button>
          </div>
        </div>
      )}

      <section aria-label="Summary" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="flex items-center gap-4 p-5">
          <ScoreRing value={s.similarity_index} tone={band.tone} label={`${s.similarity_index}%`} sub={band.label} />
          <div>
            <p className="text-sm font-semibold">Similarity index</p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Exact or near-exact overlap that needs attention.</p>
            <p className="mt-2 text-xs">
              <span className="font-semibold text-red-600 dark:text-red-400">{s.unattributed_pct}%</span> unattributed
            </p>
          </div>
        </Card>
        <Card className="p-5">
          <p className="text-sm font-semibold">Possible paraphrase</p>
          <p className={cx("mt-2 text-3xl font-bold tabular-nums", TONE_TEXT[s.semantic_pct > 15 ? "orange" : s.semantic_pct > 0 ? "amber" : "emerald"])}>{s.semantic_pct}%</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">of words in passages with strong paraphrase evidence. Reported separately; review in context.</p>
          {(report.similarity.scores.weak_semantic_pct ?? 0) > 0 && (
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">+{report.similarity.scores.weak_semantic_pct}% weak single-sentence signals (hidden by default)</p>
          )}
          {report.similarity.method.semantic_provider?.degraded && <Badge tone="amber" className="mt-2">Degraded: lexical fallback</Badge>}
        </Card>
        <Card className="p-5">
          <p className="text-sm font-semibold">AI-writing indicators</p>
          <p className={cx("mt-2 text-lg font-bold leading-tight", TONE_TEXT[aiTone(s.ai_verdict)])}>{s.ai_label}</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Banded result with uncertainty. Not proof of authorship.</p>
          <button className="mt-2 text-xs font-semibold text-brand-600 hover:underline dark:text-brand-400" onClick={() => setTab("ai")}>
            Why?
          </button>
        </Card>
        <Card className="p-5">
          <p className="text-sm font-semibold">Writing quality</p>
          <p className={cx("mt-2 text-3xl font-bold tabular-nums", TONE_TEXT[qualityTone(s.quality_score)])}>
            {Math.round(s.quality_score)}
            <span className="text-base font-medium text-slate-400">/100</span>
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {s.citation_issues} citation issue{s.citation_issues === 1 ? "" : "s"} · {report.quality.findings.length} writing suggestions
          </p>
        </Card>
      </section>

      <div className="rounded-2xl bg-slate-100/80 p-1 dark:bg-slate-900/60">
        <Tabs<TabId>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "viewer", label: "Document", count: visible.length },
            { id: "sources", label: "Sources", count: report.similarity.sources.length },
            { id: "ai", label: "AI indicators" },
            { id: "quality", label: "Writing quality" },
            { id: "citations", label: "Citations", count: report.citations.issues.length },
            { id: "coverage", label: "Coverage & method" },
          ]}
        />
      </div>

      {tab === "viewer" && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
          <Card className="p-0">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
              <Filter className="h-4 w-4 text-slate-400" aria-hidden />
              {(Object.keys(GROUP_META) as FindingGroup[]).map((g) => (
                <button
                  key={g}
                  aria-pressed={groups[g]}
                  onClick={() => setGroups((x) => ({ ...x, [g]: !x[g] }))}
                  className={cx(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset transition",
                    groups[g] ? "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600" : "text-slate-400 ring-slate-200 dark:ring-slate-800",
                  )}
                  title={GROUP_META[g].description}
                >
                  <span className={cx("h-2.5 w-4 rounded-sm underline decoration-2", GROUP_META[g].mark)} aria-hidden />
                  {GROUP_META[g].label}
                  <span className="text-slate-400">{counts[g]}</span>
                </button>
              ))}
              {weakCount > 0 && (
                <button
                  aria-pressed={showWeak}
                  onClick={() => setShowWeak(!showWeak)}
                  className={cx(
                    "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset transition",
                    showWeak ? "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600" : "text-slate-400 ring-slate-200 dark:ring-slate-800",
                  )}
                  title="Single sentences with moderate semantic similarity. Often topical rather than paraphrased; hidden by default."
                >
                  Weak semantic signals <span className="text-slate-400">{weakCount}</span>
                </button>
              )}
              <div className="ml-auto flex items-center gap-2">
                <Select aria-label="Filter by source" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="h-8 w-40 py-1 text-xs">
                  <option value="">All sources</option>
                  {report.similarity.sources.map((src) => (
                    <option key={src.id} value={src.id}>
                      {src.rank}. {src.title.slice(0, 40)}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Severity" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as "all" | "medium")} className="h-8 w-32 py-1 text-xs">
                  <option value="all">All severities</option>
                  <option value="medium">Medium and up</option>
                </Select>
              </div>
            </div>
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2 text-xs text-slate-500 dark:border-slate-800">
              <span>
                {visible.length ? `${selected ? visible.findIndex((f) => f.id === selected.id) + 1 : 0} of ${visible.length} highlighted passages` : "No passages match the filters"}
              </span>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => step(-1)} disabled={!visible.length} aria-label="Previous passage">
                  <ChevronLeft className="h-4 w-4" /> Prev
                </Button>
                <Button size="sm" variant="ghost" onClick={() => step(1)} disabled={!visible.length} aria-label="Next passage">
                  Next <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <article className="max-h-[75vh] overflow-y-auto px-5 py-6 sm:px-10" aria-label="Document text with highlights">
              <DocumentViewer text={report.text} findings={visible} selectedId={selected?.id ?? null} onSelect={select} sourceRank={sourceRank} />
            </article>
          </Card>
          <aside className="lg:sticky lg:top-20 lg:self-start">
            <Card className="max-h-[80vh] overflow-y-auto p-5">
              {selected ? (
                <FindingDetail finding={selected} text={report.text} sources={report.similarity.sources} onOpenTab={(t) => setTab(t as TabId)} />
              ) : (
                <div className="text-sm text-slate-500 dark:text-slate-400">
                  <p className="font-semibold text-slate-700 dark:text-slate-200">Select a highlighted passage</p>
                  <p className="mt-1">Click any highlight, or use Next, to see the matched source, the evidence and what to do.</p>
                  <ul className="mt-4 space-y-2">
                    {(Object.keys(GROUP_META) as FindingGroup[]).map((g) => (
                      <li key={g} className="flex items-start gap-2">
                        <span className={cx("mt-1 h-3 w-5 shrink-0 rounded-sm", GROUP_META[g].mark)} aria-hidden />
                        <span>
                          <span className="font-medium text-slate-700 dark:text-slate-200">{GROUP_META[g].label}.</span> {GROUP_META[g].description}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          </aside>
        </div>
      )}

      {tab === "sources" && <SourcesTab report={report} onShow={(id) => { setSourceFilter(id); setGroups((g) => ({ ...g, exact: true, paraphrase: true, citation: true, ok: true })); setTab("viewer"); }} />}
      {tab === "ai" && <AiTab report={report} />}
      {tab === "quality" && <QualityTab report={report} onShow={() => { setGroups((g) => ({ ...g, quality: true })); setTab("viewer"); }} />}
      {tab === "citations" && <CitationsTab report={report} />}
      {tab === "coverage" && <CoverageTab report={report} />}

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete this document?">
        <p className="text-sm text-slate-600 dark:text-slate-300">The document, its reports and revisions are permanently deleted. This cannot be undone.</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            loading={busy === "delete"}
            onClick={async () => {
              setBusy("delete");
              try {
                await api.del(`/api/documents/${doc.id}`);
                toast("Document deleted.", "success");
                navigate("/history");
              } catch (e) {
                toast(e instanceof Error ? e.message : "Delete failed.", "error");
                setBusy(null);
              }
            }}
          >
            Delete permanently
          </Button>
        </div>
      </Modal>
    </div>
  );
}

function SourcesTab({ report, onShow }: { report: Report; onShow: (id: string) => void }) {
  const rows = report.similarity.sources;
  if (!rows.length) {
    return (
      <Card>
        <EmptyState icon={<FileText className="h-6 w-6" />} title="No matching sources">
          {report.summary.coverage_gap ? "Few or no external sources could be searched; see Coverage & method." : `None of the ${report.summary.sources_compared} sources compared contained matching passages.`}
        </EmptyState>
      </Card>
    );
  }
  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {rows.map((r) => {
          const link = r.url || (r.doi ? `https://doi.org/${r.doi}` : null);
          return (
            <li key={r.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-900 text-sm font-bold text-white dark:bg-slate-100 dark:text-slate-900">{r.rank}</span>
              <div className="min-w-0 flex-1">
                <p className="font-semibold leading-snug">{r.title}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {r.provider} · {r.kind} · {r.text_kind === "full_text" ? "full text" : `${r.text_kind} only`}
                  {r.year ? ` · ${r.year}` : ""}
                  {r.license ? ` · ${r.license}` : ""}
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {Object.entries(r.categories).map(([k, v]) => (
                    <Badge key={k} tone="slate">
                      {k.replace(/_/g, " ")} × {v}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-4 sm:w-64">
                <div className="flex-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500">Counted</span>
                    <span className="font-semibold tabular-nums">{r.percent}%</span>
                  </div>
                  <Meter value={r.percent * 2} tone="red" />
                  <div className="mt-1.5 flex justify-between text-xs">
                    <span className="text-slate-500">Paraphrase</span>
                    <span className="font-semibold tabular-nums">{r.semantic_percent}%</span>
                  </div>
                  <Meter value={r.semantic_percent * 2} tone="orange" />
                </div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="subtle" onClick={() => onShow(r.id)}>
                  Show matches
                </Button>
                {link && (
                  <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 items-center gap-1 rounded-xl px-3 text-xs font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-800">
                    Open <ExternalLink className="h-3 w-3" aria-hidden />
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "?";
  return Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function AiTab({ report }: { report: Report }) {
  const ai = report.ai;
  const maxAbs = Math.max(0.5, ...ai.features.map((f) => Math.abs(f.contribution)));
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-6">
        <Card className="p-6">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={aiTone(ai.verdict)} className="text-sm">
              {ai.label}
            </Badge>
            {ai.mixed_signals && <Badge tone="amber">Mixed signals across paragraphs</Badge>}
          </div>
          {ai.explanation && <p className="mt-3 leading-relaxed">{ai.explanation}</p>}
          {ai.score !== undefined && (
            <p className="mt-2 text-xs text-slate-500">
              Model output {ai.score.toFixed(2)} on a 0–1 scale (bands: ≤ {ai.model?.threshold_low} few indicators, ≥ {ai.model?.threshold_high} many). {ai.score_note}
            </p>
          )}
          {(ai.pushing_up?.length || ai.pushing_down?.length) ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl bg-violet-50 p-3 text-sm dark:bg-violet-500/10">
                <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300">Pointing toward AI-like style</p>
                <p className="mt-1">{ai.pushing_up?.join(", ") || "—"}</p>
              </div>
              <div className="rounded-xl bg-emerald-50 p-3 text-sm dark:bg-emerald-500/10">
                <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">Pointing toward human-like style</p>
                <p className="mt-1">{ai.pushing_down?.join(", ") || "—"}</p>
              </div>
            </div>
          ) : null}
        </Card>
        {ai.features.length > 0 && (
          <Card className="p-6">
            <h2 className="font-semibold">Measured indicators</h2>
            <p className="mt-1 text-xs text-slate-500">Bars show how much each indicator moved the result (right: more AI-like, left: more human-like).</p>
            <div className="mt-4 space-y-3">
              {ai.features.map((f) => (
                <div key={f.name} className="grid grid-cols-[minmax(0,1fr)_120px] items-center gap-3 text-sm sm:grid-cols-[220px_minmax(0,1fr)_140px]">
                  <div>
                    <p className="font-medium">{f.label}</p>
                    <p className="text-xs text-slate-500 sm:hidden">{f.description}</p>
                  </div>
                  <div className="relative hidden h-2.5 rounded-full bg-slate-100 dark:bg-slate-800 sm:block" title={f.description}>
                    <span className="absolute left-1/2 top-[-3px] h-4 w-px bg-slate-300 dark:bg-slate-600" />
                    <span
                      className={cx("absolute top-0 h-2.5 rounded-full", f.contribution >= 0 ? "bg-violet-500" : "bg-emerald-500")}
                      style={f.contribution >= 0 ? { left: "50%", width: `${(Math.abs(f.contribution) / maxAbs) * 50}%` } : { right: "50%", width: `${(Math.abs(f.contribution) / maxAbs) * 50}%` }}
                    />
                  </div>
                  <p className="text-right text-xs tabular-nums text-slate-500">
                    <span className="font-semibold text-slate-800 dark:text-slate-100">{fmtNum(f.value)}</span>
                    <br />
                    human ~{fmtNum(f.typical_human)} · AI ~{fmtNum(f.typical_ai)}
                  </p>
                </div>
              ))}
            </div>
          </Card>
        )}
        {ai.paragraphs.length > 0 && (
          <Card className="p-6">
            <h2 className="font-semibold">Paragraph-level results</h2>
            <ul className="mt-3 space-y-2">
              {ai.paragraphs.map((p) => (
                <li key={p.index} className="flex items-start justify-between gap-3 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-950/60">
                  <span className="line-clamp-2 font-serif text-slate-600 dark:text-slate-300">{report.text.slice(p.start, Math.min(p.end, p.start + 220))}…</span>
                  <Badge tone={aiTone(p.verdict)} className="shrink-0">
                    {p.verdict === "many_indicators" ? "Many" : p.verdict === "few_indicators" ? "Few" : "Unclear"}
                  </Badge>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
      <aside className="space-y-6">
        <Card className="p-5">
          <h2 className="flex items-center gap-2 font-semibold">
            <Info className="h-4 w-4 text-brand-500" aria-hidden /> Read before acting
          </h2>
          <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-300">
            {ai.caveats.map((c) => (
              <li key={c} className="flex gap-2">
                <span aria-hidden>•</span>
                {c}
              </li>
            ))}
          </ul>
        </Card>
        {ai.model && (
          <Card className="p-5 text-sm">
            <h2 className="font-semibold">About the model</h2>
            <p className="mt-2 text-slate-600 dark:text-slate-300">Logistic regression over {ai.features.length} stylometric features, trained on {ai.model.trained_on}.</p>
            <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
              {Object.entries(ai.model.cv_metrics)
                .filter(([k]) => ["precision_many", "recall_many", "false_positive_rate_many", "auc", "ece"].includes(k))
                .map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-slate-50 p-2 dark:bg-slate-950/60">
                    <dt className="text-slate-500">{k.replace("_many", "").replace(/_/g, " ")}</dt>
                    <dd className="font-semibold tabular-nums">{v}</dd>
                  </div>
                ))}
            </dl>
            <p className="mt-3 text-xs text-slate-500">{ai.model.limitations}</p>
          </Card>
        )}
      </aside>
    </div>
  );
}

function QualityTab({ report, onShow }: { report: Report; onShow: () => void }) {
  const q = report.quality;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card className="p-6">
        <div className="flex items-center gap-5">
          <ScoreRing value={q.score} tone={qualityTone(q.score)} label={String(Math.round(q.score))} sub="of 100" />
          <div className="text-sm text-slate-600 dark:text-slate-300">
            <p className="font-semibold text-slate-900 dark:text-white">Writing quality index</p>
            <p className="mt-1">Average of the four subscores below. Engine: {q.engine}.</p>
            <Button size="sm" variant="subtle" className="mt-3" onClick={onShow}>
              Show suggestions in document
            </Button>
          </div>
        </div>
        <div className="mt-6 space-y-4">
          {Object.entries(q.subscores).map(([k, v]) => (
            <div key={k}>
              <div className="flex justify-between text-sm">
                <span className="font-medium capitalize">{k}</span>
                <span className="font-semibold tabular-nums">{v}</span>
              </div>
              <Meter value={v} tone={qualityTone(v)} />
              <p className="mt-1 text-xs text-slate-500">{q.formulas[k]}</p>
            </div>
          ))}
        </div>
      </Card>
      <Card className="p-6">
        <h2 className="font-semibold">Readability</h2>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
          {Object.entries(q.readability).map(([k, v]) => (
            <div key={k} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-950/60">
              <dt className="text-xs text-slate-500">{k.replace(/_/g, " ")}</dt>
              <dd className="text-lg font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
        <h2 className="mt-6 font-semibold">Counts</h2>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
          {Object.entries(q.counts).map(([k, v]) => (
            <div key={k} className="flex justify-between rounded-xl bg-slate-50 px-3 py-2 dark:bg-slate-950/60">
              <dt className="text-slate-500">{k.replace(/_/g, " ")}</dt>
              <dd className="font-semibold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  );
}

function CitationsTab({ report }: { report: Report }) {
  const c = report.citations;
  const statusTone = (st?: string) => (st === "verified" || st === "found" ? "emerald" : st === "not_found" || st === "mismatch" ? "red" : "slate");
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card className="p-6">
        <h2 className="font-semibold">Issues</h2>
        <p className="mt-1 text-sm text-slate-500">
          Style: {c.style ?? "none detected"} · {c.citations.length} in-text citations · {c.references.length} reference entries
        </p>
        {c.issues.length ? (
          <ul className="mt-4 space-y-2">
            {c.issues.map((i, n) => (
              <li key={n} className="flex gap-3 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-950/60">
                <Badge tone={i.severity === "medium" || i.severity === "high" ? "amber" : "slate"} className="h-fit shrink-0">
                  {i.type.replace(/_/g, " ")}
                </Badge>
                <span>{i.message}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-4 text-sm text-emerald-700 dark:text-emerald-400">No citation problems were detected.</p>
        )}
      </Card>
      <Card className="p-6">
        <h2 className="font-semibold">Reference list</h2>
        {c.references.length ? (
          <ul className="mt-4 space-y-3">
            {c.references.map((r) => (
              <li key={r.index} className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-800">
                <p className="font-serif leading-snug">{r.text}</p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge tone={r.cited ? "emerald" : "amber"}>{r.cited ? "Cited in text" : "Not cited in text"}</Badge>
                  {r.doi && <Badge tone="slate">DOI {r.doi}</Badge>}
                  {r.verification && <Badge tone={statusTone(r.verification.status)}>Registry: {r.verification.status.replace("_", " ")}</Badge>}
                </div>
                {r.verification?.reason && <p className="mt-1 text-xs text-slate-500">{r.verification.reason}</p>}
                {r.verification?.title && r.verification.status !== "not_found" && <p className="mt-1 text-xs text-slate-500">Registry title: {r.verification.title}</p>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-4 text-sm text-slate-500">No reference list was found (look for a heading such as “References” or “Bibliography”).</p>
        )}
      </Card>
    </div>
  );
}

function CoverageTab({ report }: { report: Report }) {
  const cov = report.coverage;
  const m = report.similarity.method;
  const statusTone = (s: string) => (s === "ok" ? "emerald" : s === "degraded" ? "amber" : s === "unavailable" ? "red" : "slate");
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card className="p-6 lg:col-span-2">
        <h2 className="font-semibold">What was searched</h2>
        <p className="mt-1 text-sm">{cov.summary}</p>
        {cov.data_shared && <p className="mt-1 text-xs text-slate-500">{cov.data_shared}</p>}
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-4">Service</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4 text-right">Queries</th>
                <th className="py-2 pr-4 text-right">Results</th>
                <th className="py-2">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {cov.providers.map((p) => (
                <tr key={p.name}>
                  <td className="py-2 pr-4 font-medium">{p.label}</td>
                  <td className="py-2 pr-4">
                    <Badge tone={statusTone(p.status)}>{p.status.replace("_", " ")}</Badge>
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums">{p.queries ?? "–"}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{p.results ?? "–"}</td>
                  <td className="py-2 text-xs text-slate-500">{p.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 className="mt-6 text-sm font-semibold">Not covered by any automated checker</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600 dark:text-slate-300">
          {cov.not_covered.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Card>
      <Card className="p-6">
        <h2 className="font-semibold">How scores were calculated</h2>
        <dl className="mt-3 space-y-3 text-sm">
          <div>
            <dt className="font-medium">Similarity index</dt>
            <dd className="text-slate-600 dark:text-slate-300">{m.formula}</dd>
          </div>
          <div>
            <dt className="font-medium">Exact matching</dt>
            <dd className="text-slate-600 dark:text-slate-300">
              {m.shingle_size}-word fingerprints, minimum run of {m.min_match_words} words; quotes {m.exclude_quotes ? "with citation not counted" : "counted"}; reference list {m.exclude_bibliography ? "not counted" : "counted"}.
            </dd>
          </div>
          <div>
            <dt className="font-medium">Semantic matching</dt>
            <dd className="text-slate-600 dark:text-slate-300">
              {m.semantic_provider
                ? `${m.semantic_provider.label}; paraphrase threshold ${m.semantic_provider.paraphrase_threshold}, strong ${m.semantic_provider.strong_threshold}${m.reranker ? `; reranked with ${m.reranker}` : ""}.`
                : "Not run."}
              {m.semantic_provider?.degraded && " No semantic model was available, so only shared vocabulary could be compared."}
            </dd>
          </div>
        </dl>
      </Card>
      <Card className="p-6">
        <h2 className="font-semibold">Search queries generated</h2>
        <p className="mt-1 text-xs text-slate-500">Only these excerpts were sent to search services.</p>
        <ul className="mt-3 max-h-72 space-y-1.5 overflow-y-auto text-sm">
          {report.queries.map((q) => (
            <li key={q.id} className="flex gap-2">
              <Badge tone="slate" className="h-fit shrink-0">
                {q.family}
              </Badge>
              <span className="text-slate-600 dark:text-slate-300">{q.text}</span>
            </li>
          ))}
        </ul>
        <h3 className="mt-5 text-sm font-semibold">Processing time</h3>
        <p className="mt-1 text-xs text-slate-500">
          {Object.entries(report.timings)
            .map(([k, v]) => `${k} ${v.toFixed(2)} s`)
            .join(" · ")}
        </p>
      </Card>
    </div>
  );
}
