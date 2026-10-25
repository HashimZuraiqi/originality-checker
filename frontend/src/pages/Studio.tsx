import { AlertTriangle, Check, Download, GitCompare, History as HistoryIcon, Info, RotateCcw, Save, ScanSearch, ShieldAlert, Sparkles, Undo2, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageLoader, Select, Textarea, cx } from "../components/ui";
import { api, download, type Change, type DocumentItem, type Job, type Revision, type RewriteResult } from "../lib/api";
import { useToast } from "../lib/app-state";
import { applyChanges, wordDiff } from "../lib/diff";
import { formatDate } from "../lib/format";

type Status = { llm_configured: boolean; llm_allowed: boolean; modes: Record<string, string>; disclosure: string };

const MODE_HINTS: Record<string, string> = {
  natural: "Varied rhythm, plain words, no stock phrases",
  academic: "Formal register, precise and cautious",
  professional: "Clear, direct, business-appropriate",
  simple: "Short sentences, everyday words",
  concise: "Remove redundancy and filler",
  detailed: "Clarify reasoning (AI assistant only)",
};

export default function Studio() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const editor = useRef<HTMLTextAreaElement>(null);

  const [status, setStatus] = useState<Status | null>(null);
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [loading, setLoading] = useState(Boolean(docId));
  const [original, setOriginal] = useState("");
  const [base, setBase] = useState("");
  const [undo, setUndo] = useState<string[]>([]);
  const [mode, setMode] = useState("natural");
  const [engine, setEngine] = useState<"auto" | "rules" | "llm">("auto");
  const [result, setResult] = useState<RewriteResult | null>(null);
  const [resultBase, setResultBase] = useState("");
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [saveOpen, setSaveOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [compare, setCompare] = useState<{ title: string; a: string; b: string } | null>(null);
  const [exportFmt, setExportFmt] = useState<"docx" | "pdf" | "txt">("docx");

  useEffect(() => {
    api.get<Status>("/api/assistant/status").then(setStatus).catch(() => {});
  }, []);

  const loadRevisions = useCallback(async () => {
    if (!docId) return;
    const d = await api.get<{ document: DocumentItem; revisions: Revision[] }>(`/api/documents/${docId}`);
    setDoc(d.document);
    setRevisions(d.revisions);
  }, [docId]);

  useEffect(() => {
    if (!docId) return;
    (async () => {
      try {
        await loadRevisions();
        const t = await api.get<{ text: string }>(`/api/documents/${docId}/text`);
        setOriginal(t.text);
        setBase(t.text);
      } catch (e) {
        toast(e instanceof Error ? e.message : "Could not load the document.", "error");
      } finally {
        setLoading(false);
      }
    })();
  }, [docId, loadRevisions, toast]);

  // Suggestions refer to the text they were generated for.
  const stale = result !== null && resultBase !== base;
  const accepted = useMemo(() => new Set(Object.entries(decisions).filter(([, v]) => v).map(([k]) => k)), [decisions]);
  const preview = useMemo(() => (result && !stale ? applyChanges(base, result.changes, accepted) : base), [result, stale, base, accepted]);

  const commit = (next: string) => {
    if (next === base) return;
    setUndo((u) => [...u.slice(-49), base]);
    setBase(next);
  };

  const runRewrite = async (scope: "selection" | "all") => {
    if (!base.trim()) {
      toast("Add some text first.", "error");
      return;
    }
    const range = scope === "selection" && selection && selection.end > selection.start ? selection : { start: 0, end: base.length };
    // Pasted text (no saved document): the first rewrite fixes the "original".
    if (!docId && !original) setOriginal(base);
    setBusy("rewrite");
    try {
      const r = await api.post<RewriteResult>("/api/assistant/rewrite", {
        mode,
        engine,
        text: base,
        start: range.start,
        end: range.end,
      });
      setResult(r);
      setResultBase(base);
      setDecisions(Object.fromEntries(r.changes.map((c) => [c.id, !c.blocked])));
      if (r.notice) toast(r.notice, "info");
      if (!r.changes.length) toast("No changes were suggested for this mode.", "info");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Rewrite failed.", "error");
    } finally {
      setBusy(null);
    }
  };

  const applyDecisions = () => {
    if (!result) return;
    commit(applyChanges(base, result.changes, accepted));
    setResult(null);
    toast(`Applied ${accepted.size} change${accepted.size === 1 ? "" : "s"}.`, "success");
  };

  const setAll = (v: boolean) => result && setDecisions(Object.fromEntries(result.changes.map((c) => [c.id, v && !c.blocked])));

  if (loading) return <PageLoader />;

  const llmReady = status?.llm_configured && status.llm_allowed;
  const selectedWords = selection ? base.slice(selection.start, selection.end).trim().split(/\s+/).filter(Boolean).length : 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Writing studio</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {doc ? (
              <>
                Improving <Link to={`/documents/${doc.id}`} className="font-medium text-brand-600 hover:underline dark:text-brand-400">{doc.title}</Link>. Your original is never overwritten.
              </>
            ) : (
              "Paste text on the left, choose a style, and review every suggested change."
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" icon={<Undo2 className="h-4 w-4" />} disabled={!undo.length} onClick={() => {
            const prev = undo[undo.length - 1];
            setUndo((u) => u.slice(0, -1));
            setBase(prev);
            setResult(null);
          }}>
            Undo
          </Button>
          <Button variant="secondary" size="sm" icon={<RotateCcw className="h-4 w-4" />} disabled={!original || base === original} onClick={() => {
            commit(original);
            setResult(null);
          }}>
            Restore original
          </Button>
          {docId && (
            <>
              <Button variant="secondary" size="sm" icon={<Save className="h-4 w-4" />} onClick={() => setSaveOpen(true)}>
                Save version
              </Button>
              <Button variant="secondary" size="sm" icon={<GitCompare className="h-4 w-4" />} disabled={base === original} onClick={() => setCompare({ title: "Original → current", a: original, b: base })}>
                Compare
              </Button>
            </>
          )}
          <div className="flex items-center gap-1">
            <Select aria-label="Export format" value={exportFmt} onChange={(e) => setExportFmt(e.target.value as "docx" | "pdf" | "txt")} className="h-8 w-24 py-1 text-xs">
              <option value="docx">Word</option>
              <option value="pdf">PDF</option>
              <option value="txt">Text</option>
            </Select>
            <Button size="sm" variant="secondary" icon={<Download className="h-4 w-4" />} loading={busy === "export"} disabled={!base.trim()} onClick={async () => {
              setBusy("export");
              try {
                await download("POST", "/api/export/text", { title: doc ? `${doc.title} (revised)` : "Revised document", text: base, format: exportFmt }, `revised.${exportFmt}`);
              } catch (e) {
                toast(e instanceof Error ? e.message : "Export failed.", "error");
              } finally {
                setBusy(null);
              }
            }}>
              Export
            </Button>
          </div>
        </div>
      </div>

      <Card className="flex flex-wrap items-center gap-3 p-3">
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Writing mode">
          {Object.entries(status?.modes ?? MODE_HINTS).map(([id, name]) => (
            <button
              key={id}
              role="radio"
              aria-checked={mode === id}
              onClick={() => setMode(id)}
              title={MODE_HINTS[id]}
              className={cx(
                "rounded-xl px-3 py-1.5 text-sm font-medium transition",
                mode === id ? "bg-brand-600 text-white shadow-sm" : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700",
              )}
            >
              {typeof name === "string" && name.length < 40 ? name : id}
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select aria-label="Editor engine" value={engine} onChange={(e) => setEngine(e.target.value as "auto" | "rules" | "llm")} className="h-9 w-48 py-1 text-xs">
            <option value="auto">{llmReady ? "AI assistant (Claude)" : "Best available"}</option>
            <option value="rules">Rule-based editor (on-server)</option>
            <option value="llm" disabled={!llmReady}>
              AI assistant only
            </option>
          </Select>
          <Button variant="secondary" icon={<Wand2 className="h-4 w-4" />} disabled={!selectedWords || busy !== null} loading={busy === "rewrite" && !!selectedWords} onClick={() => runRewrite("selection")}>
            Rewrite selection{selectedWords ? ` (${selectedWords} words)` : ""}
          </Button>
          <Button icon={<Sparkles className="h-4 w-4" />} disabled={busy !== null} loading={busy === "rewrite" && !selectedWords} onClick={() => runRewrite("all")}>
            Rewrite all
          </Button>
        </div>
      </Card>

      {!llmReady && status && (
        <p className="flex items-start gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-300">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {status.llm_configured
            ? "The AI assistant is turned off in your settings; the on-server rule-based editor will be used (it tightens wording and tone but does not restructure arguments)."
            : "The AI assistant is not configured on this server (no API key). The rule-based editor tightens wording and adjusts tone; detailed rewriting needs the AI assistant."}
        </p>
      )}
      {llmReady && status && <p className="text-xs text-slate-500">{status.disclosure}</p>}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="flex flex-col p-0">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
            <p className="text-sm font-semibold">Your text</p>
            <span className="text-xs text-slate-500">{base.trim() ? base.trim().split(/\s+/).length : 0} words · select a passage to rewrite only that part</span>
          </div>
          <Textarea
            ref={editor}
            aria-label="Your text (editable)"
            value={base}
            onChange={(e) => setBase(e.target.value)}
            onSelect={(e) => {
              const t = e.currentTarget;
              setSelection(t.selectionEnd > t.selectionStart ? { start: t.selectionStart, end: t.selectionEnd } : null);
            }}
            placeholder="Paste or type text here…"
            className="min-h-[60vh] flex-1 rounded-none rounded-b-2xl font-serif text-[15.5px] leading-[1.8] ring-0 focus:ring-0"
          />
        </Card>

        <Card className="flex flex-col p-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-2.5 dark:border-slate-800">
            <p className="text-sm font-semibold">
              Suggested version
              {result && (
                <Badge tone={result.engine === "llm" ? "violet" : "slate"} className="ml-2">
                  {result.engine === "llm" ? "AI assistant" : "Rule-based editor"}
                </Badge>
              )}
            </p>
            {result && !stale && result.changes.length > 0 && (
              <div className="flex gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
                  Accept all
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setAll(false)}>
                  Reject all
                </Button>
                <Button size="sm" icon={<Check className="h-3.5 w-3.5" />} onClick={applyDecisions}>
                  Apply {accepted.size}
                </Button>
              </div>
            )}
          </div>
          <div className="max-h-[60vh] min-h-[60vh] flex-1 overflow-y-auto px-5 py-4">
            {!result ? (
              <EmptyState icon={<Sparkles className="h-6 w-6" />} title="No suggestions yet">
                Choose a mode and press <strong>Rewrite all</strong>, or select a passage on the left and rewrite just that part. Suggestions appear here for you to accept or reject.
              </EmptyState>
            ) : stale ? (
              <EmptyState icon={<AlertTriangle className="h-6 w-6" />} title="Text changed" action={<Button size="sm" onClick={() => runRewrite("all")}>Generate new suggestions</Button>}>
                You edited the text after these suggestions were made, so they no longer line up.
              </EmptyState>
            ) : (
              <ProposedText base={base} changes={result.changes} decisions={decisions} onToggle={(id) => setDecisions((d) => ({ ...d, [id]: !d[id] }))} />
            )}
          </div>
        </Card>
      </div>

      {result && !stale && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Card className="p-5">
            <h2 className="font-semibold">Changes ({result.changes.length})</h2>
            <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto">
              {result.changes.map((c) => (
                <li key={c.id} className={cx("rounded-xl border p-3 text-sm", c.blocked ? "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10" : "border-slate-200 dark:border-slate-800")}>
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0">
                      <del className="rounded bg-red-100 px-1 text-red-800 decoration-red-500 dark:bg-red-500/20 dark:text-red-200">{c.original || "∅"}</del>{" "}
                      → <ins className="rounded bg-emerald-100 px-1 text-emerald-800 no-underline dark:bg-emerald-500/20 dark:text-emerald-200">{c.replacement || "(removed)"}</ins>
                    </p>
                    <div className="flex shrink-0 gap-1">
                      <button aria-label="Accept change" aria-pressed={decisions[c.id]} onClick={() => setDecisions((d) => ({ ...d, [c.id]: true }))} className={cx("rounded-lg p-1.5", decisions[c.id] ? "bg-emerald-600 text-white" : "text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800")}>
                        <Check className="h-4 w-4" />
                      </button>
                      <button aria-label="Reject change" aria-pressed={!decisions[c.id]} onClick={() => setDecisions((d) => ({ ...d, [c.id]: false }))} className={cx("rounded-lg p-1.5", !decisions[c.id] ? "bg-slate-700 text-white dark:bg-slate-200 dark:text-slate-900" : "text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800")}>
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{c.reason}</p>
                  {c.warning && (
                    <p className="mt-1 flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-300">
                      <ShieldAlert className="h-3.5 w-3.5" aria-hidden /> {c.warning}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Card>
          <Card className="p-5">
            <h2 className="font-semibold">Why these changes</h2>
            <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-300">
              {result.explanations.map((e, i) => (
                <li key={i} className="flex gap-2">
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" aria-hidden />
                  {e}
                </li>
              ))}
            </ul>
            {result.concerns.length > 0 && (
              <>
                <h3 className="mt-4 text-sm font-semibold">Please check</h3>
                <ul className="mt-2 space-y-1.5 text-sm text-amber-800 dark:text-amber-300">
                  {result.concerns.map((c, i) => (
                    <li key={i} className="flex gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                      {c}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-4 text-xs text-slate-500">
              Rewording does not remove the need to cite: if a passage came from a source, keep the citation. The goal is clearer writing in your own voice, not avoiding detection.
            </p>
          </Card>
        </div>
      )}

      {preview !== base && result && !stale && (
        <p className="text-xs text-slate-500">
          Preview: {preview.trim().split(/\s+/).length} words after applying the accepted changes (now {base.trim().split(/\s+/).length}).
        </p>
      )}

      {docId && (
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 font-semibold">
              <HistoryIcon className="h-4 w-4" aria-hidden /> Saved versions
            </h2>
            <Button size="sm" variant="subtle" icon={<ScanSearch className="h-4 w-4" />} disabled={base === original || busy !== null} loading={busy === "recheck"} onClick={async () => {
              setBusy("recheck");
              try {
                const form = new FormData();
                form.append("text", base);
                form.append("title", `${doc?.title ?? "Document"} (revised)`);
                if (doc?.collection_id) form.append("collection_id", doc.collection_id);
                if (doc?.author_name) form.append("author_name", doc.author_name);
                const r = await api.post<{ job: Job }>("/api/documents", form);
                navigate(`/analysis/${r.job.id}`);
              } catch (e) {
                toast(e instanceof Error ? e.message : "Could not start the check.", "error");
                setBusy(null);
              }
            }}>
              Check this version
            </Button>
          </div>
          {revisions.length ? (
            <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
              {revisions.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                  <span>
                    <span className="font-medium">{r.label}</span> <span className="text-slate-500">· {r.word_count} words · {formatDate(r.created_at)}</span>
                  </span>
                  <span className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={async () => {
                      const rev = await api.get<Revision & { text: string }>(`/api/revisions/${r.id}`);
                      setCompare({ title: `${r.label} → current`, a: rev.text, b: base });
                    }}>
                      Compare
                    </Button>
                    <Button size="sm" variant="ghost" onClick={async () => {
                      const rev = await api.get<Revision & { text: string }>(`/api/revisions/${r.id}`);
                      commit(rev.text);
                      setResult(null);
                      toast(`Loaded “${r.label}”. Use Undo to go back.`, "info");
                    }}>
                      Load
                    </Button>
                    <Button size="sm" variant="ghost" aria-label={`Delete version ${r.label}`} onClick={async () => {
                      await api.del(`/api/revisions/${r.id}`);
                      await loadRevisions();
                    }}>
                      <X className="h-4 w-4" />
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-slate-500">No saved versions yet. Save a version to keep a snapshot you can compare or return to.</p>
          )}
        </Card>
      )}

      <Modal open={saveOpen} onClose={() => setSaveOpen(false)} title="Save this version">
        <Field label="Label" htmlFor="rev-label">
          <Input id="rev-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. After concise pass" maxLength={200} />
        </Field>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setSaveOpen(false)}>
            Cancel
          </Button>
          <Button loading={busy === "save"} onClick={async () => {
            setBusy("save");
            try {
              await api.post(`/api/documents/${docId}/revisions`, { text: base, label: label || `Version ${revisions.length + 1}` });
              await loadRevisions();
              setSaveOpen(false);
              setLabel("");
              toast("Version saved.", "success");
            } catch (e) {
              toast(e instanceof Error ? e.message : "Save failed.", "error");
            } finally {
              setBusy(null);
            }
          }}>
            Save
          </Button>
        </div>
      </Modal>

      <Modal open={compare !== null} onClose={() => setCompare(null)} title={compare?.title ?? ""} wide>
        {compare && <Comparison a={compare.a} b={compare.b} />}
      </Modal>
    </div>
  );
}

function ProposedText({ base, changes, decisions, onToggle }: { base: string; changes: Change[]; decisions: Record<string, boolean>; onToggle: (id: string) => void }) {
  const parts: JSX.Element[] = [];
  let pos = 0;
  for (const c of [...changes].sort((a, b) => a.start - b.start)) {
    if (c.start < pos) continue;
    parts.push(<span key={`t${pos}`}>{base.slice(pos, c.start)}</span>);
    const on = decisions[c.id];
    parts.push(
      <button
        key={c.id}
        onClick={() => onToggle(c.id)}
        title={`${c.reason}${c.warning ? " — " + c.warning : ""} (click to ${on ? "reject" : "accept"})`}
        aria-pressed={on}
        className={cx("rounded px-0.5 text-left transition hover:ring-2 hover:ring-brand-300", c.blocked && "ring-1 ring-amber-400")}
      >
        {c.original && <del className={cx("decoration-red-500 decoration-2", on ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300" : "hidden")}>{c.original}</del>}
        {!on && <span className="bg-slate-100 dark:bg-slate-800">{c.original}</span>}
        {on && c.replacement && <ins className="bg-emerald-100 text-emerald-800 no-underline dark:bg-emerald-500/20 dark:text-emerald-200">{c.replacement}</ins>}
      </button>,
    );
    pos = c.end;
  }
  parts.push(<span key="tail">{base.slice(pos)}</span>);
  return <div className="whitespace-pre-wrap font-serif text-[15.5px] leading-[1.8]">{parts}</div>;
}

function Comparison({ a, b }: { a: string; b: string }) {
  const parts = useMemo(() => wordDiff(a, b), [a, b]);
  if (!parts) return <p className="text-sm text-slate-500">These versions differ too much to compare word by word.</p>;
  const added = parts.filter((p) => p.type === "add").reduce((n, p) => n + p.text.trim().split(/\s+/).filter(Boolean).length, 0);
  const removed = parts.filter((p) => p.type === "del").reduce((n, p) => n + p.text.trim().split(/\s+/).filter(Boolean).length, 0);
  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">
        <span className="font-semibold text-emerald-600">+{added}</span> words added · <span className="font-semibold text-red-600">−{removed}</span> words removed
      </p>
      <div className="max-h-[65vh] overflow-y-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-4 font-serif text-[15px] leading-[1.8] dark:bg-slate-950/60">
        {parts.map((p, i) =>
          p.type === "same" ? (
            <span key={i}>{p.text}</span>
          ) : p.type === "add" ? (
            <ins key={i} className="bg-emerald-100 text-emerald-800 no-underline dark:bg-emerald-500/20 dark:text-emerald-200">
              {p.text}
            </ins>
          ) : (
            <del key={i} className="bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300">
              {p.text}
            </del>
          ),
        )}
      </div>
    </div>
  );
}
