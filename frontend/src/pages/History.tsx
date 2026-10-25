import { FileSearch, FileText, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Input, Modal, PageLoader, SectionTitle, Select } from "../components/ui";
import { api, type DocumentItem } from "../lib/api";
import { useToast } from "../lib/app-state";
import { aiTone, formatBytes, relativeTime, similarityBand, type Tone } from "../lib/format";

const STATUS_TONE: Record<DocumentItem["status"], Tone> = {
  uploaded: "slate",
  queued: "slate",
  analyzing: "brand",
  completed: "emerald",
  failed: "red",
  cancelled: "slate",
};

export function DocumentRow({ d, onDelete }: { d: DocumentItem; onDelete?: (d: DocumentItem) => void }) {
  const href = d.status === "completed" ? `/documents/${d.id}` : d.latest_job_id && (d.status === "queued" || d.status === "analyzing") ? `/analysis/${d.latest_job_id}` : `/documents/${d.id}`;
  const band = d.summary ? similarityBand(d.summary.similarity_index) : null;
  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          <FileText className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <Link to={href} className="block truncate font-semibold hover:text-brand-600 dark:hover:text-brand-400">
            {d.title}
          </Link>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            {d.author_name && <>{d.author_name} · </>}
            {d.word_count ? `${d.word_count.toLocaleString()} words · ` : ""}
            {d.file_type.toUpperCase()}
            {d.size_bytes ? ` · ${formatBytes(d.size_bytes)}` : ""} · {relativeTime(d.created_at)}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {d.late && <Badge tone="red">Late</Badge>}
        {d.summary && band ? (
          <>
            <Badge tone={band.tone}>{d.summary.similarity_index}% similarity</Badge>
            {d.summary.semantic_pct > 0 && <Badge tone="orange">{d.summary.semantic_pct}% paraphrase</Badge>}
            <Badge tone={aiTone(d.summary.ai_verdict)}>AI: {d.summary.ai_label.replace(" AI-writing indicators", "").replace("Not enough text to analyse", "n/a")}</Badge>
            {d.summary.coverage_gap && <Badge tone="amber">Limited coverage</Badge>}
          </>
        ) : (
          <Badge tone={STATUS_TONE[d.status]}>{d.status}</Badge>
        )}
        {onDelete && (
          <Button size="sm" variant="ghost" aria-label={`Delete ${d.title}`} onClick={() => onDelete(d)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
      </div>
    </li>
  );
}

export default function HistoryPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [items, setItems] = useState<DocumentItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [toDelete, setToDelete] = useState<DocumentItem | null>(null);
  const limit = 25;

  useEffect(() => {
    const t = setTimeout(async () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (q.trim()) params.set("q", q.trim());
      if (status) params.set("status", status);
      try {
        const r = await api.get<{ total: number; items: DocumentItem[] }>(`/api/documents?${params}`);
        setItems(r.items);
        setTotal(r.total);
      } catch (e) {
        toast(e instanceof Error ? e.message : "Could not load history.", "error");
        setItems([]);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [q, status, offset, toast]);

  return (
    <div>
      <SectionTitle title="History" subtitle="Every document you have checked, with its latest results." action={<Button onClick={() => navigate("/")}>New check</Button>} />
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-slate-200 p-4 dark:border-slate-800 sm:flex-row">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden />
            <Input aria-label="Search documents" placeholder="Search by title, author or file name" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} className="pl-9" />
          </div>
          <Select aria-label="Filter by status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} className="sm:w-44">
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="analyzing">Analysing</option>
            <option value="queued">Queued</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </div>
        {items === null ? (
          <PageLoader />
        ) : items.length === 0 ? (
          <EmptyState icon={<FileSearch className="h-6 w-6" />} title={q || status ? "No documents match" : "No documents yet"} action={!q && !status ? <Button onClick={() => navigate("/")}>Check your first document</Button> : undefined}>
            {q || status ? "Try a different search or filter." : "Upload a document to see it here."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {items.map((d) => (
              <DocumentRow key={d.id} d={d} onDelete={setToDelete} />
            ))}
          </ul>
        )}
        {total > limit && (
          <div className="flex items-center justify-between border-t border-slate-200 p-3 text-sm dark:border-slate-800">
            <span className="text-slate-500">
              {offset + 1}–{Math.min(offset + limit, total)} of {total}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                Previous
              </Button>
              <Button size="sm" variant="secondary" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Modal open={toDelete !== null} onClose={() => setToDelete(null)} title="Delete document?">
        <p className="text-sm text-slate-600 dark:text-slate-300">“{toDelete?.title}” and all its reports and versions will be permanently deleted.</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setToDelete(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              if (!toDelete) return;
              try {
                await api.del(`/api/documents/${toDelete.id}`);
                setItems((xs) => (xs ? xs.filter((x) => x.id !== toDelete.id) : xs));
                setTotal((t) => t - 1);
                toast("Deleted.", "success");
              } catch (e) {
                toast(e instanceof Error ? e.message : "Delete failed.", "error");
              }
              setToDelete(null);
            }}
          >
            Delete
          </Button>
        </div>
      </Modal>
    </div>
  );
}
