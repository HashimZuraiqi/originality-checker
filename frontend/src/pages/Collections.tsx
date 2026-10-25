import { ArrowLeft, CalendarClock, FolderKanban, Plus, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageLoader, SectionTitle, Textarea, Toggle } from "../components/ui";
import { api, type Collection, type DocumentItem } from "../lib/api";
import { useToast } from "../lib/app-state";
import { formatDate } from "../lib/format";
import { DocumentRow } from "./History";

type Draft = { title: string; instructions: string; due_date: string; min_match_words: number; exclude_quotes: boolean; exclude_bibliography: boolean };
const EMPTY: Draft = { title: "", instructions: "", due_date: "", min_match_words: 8, exclude_quotes: true, exclude_bibliography: true };

function toPayload(d: Draft) {
  return { ...d, due_date: d.due_date ? new Date(d.due_date).toISOString() : null };
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function CollectionForm({ initial, onSubmit, submitLabel }: { initial: Draft; onSubmit: (d: Draft) => Promise<void>; submitLabel: string }) {
  const [d, setD] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!d.title.trim()) {
      setError("Give the collection a title.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Title" htmlFor="c-title">
        <Input id="c-title" value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} maxLength={200} placeholder="e.g. HIST 201 — Essay 2" />
      </Field>
      <Field label="Instructions (optional)" htmlFor="c-instr">
        <Textarea id="c-instr" value={d.instructions} onChange={(e) => setD({ ...d, instructions: e.target.value })} className="min-h-[90px]" maxLength={5000} />
      </Field>
      <Field label="Due date (optional)" htmlFor="c-due" hint="Documents added after this time are marked late.">
        <Input id="c-due" type="datetime-local" value={d.due_date} onChange={(e) => setD({ ...d, due_date: e.target.value })} />
      </Field>
      <Field label={`Minimum match length: ${d.min_match_words} words`} htmlFor="c-min">
        <input id="c-min" type="range" min={5} max={30} value={d.min_match_words} onChange={(e) => setD({ ...d, min_match_words: Number(e.target.value) })} className="w-full accent-brand-600" />
      </Field>
      <Toggle checked={d.exclude_quotes} onChange={(v) => setD({ ...d, exclude_quotes: v })} label="Don't count quoted and cited text" />
      <Toggle checked={d.exclude_bibliography} onChange={(v) => setD({ ...d, exclude_bibliography: v })} label="Don't count reference lists" />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex justify-end">
        <Button type="submit" loading={busy}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

export function CollectionsList() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Collection[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api.get<Collection[]>("/api/collections").then(setItems).catch(() => setItems([]));
  }, []);
  return (
    <div>
      <SectionTitle
        title="Collections"
        subtitle="Group documents (for example an assignment). Documents in a collection are also compared with each other."
        action={
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>
            New collection
          </Button>
        }
      />
      {items === null ? (
        <PageLoader />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon={<FolderKanban className="h-6 w-6" />} title="No collections yet" action={<Button onClick={() => setOpen(true)}>Create a collection</Button>}>
            Instructors can create one per assignment, set a due date and compare all submissions against each other and against external sources.
          </EmptyState>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((c) => (
            <Link key={c.id} to={`/collections/${c.id}`} className="rounded-2xl bg-white p-5 shadow-card ring-1 ring-slate-200/80 transition hover:-translate-y-0.5 hover:shadow-lift dark:bg-slate-900 dark:ring-slate-800">
              <div className="flex items-start justify-between gap-3">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
                  <FolderKanban className="h-5 w-5" aria-hidden />
                </span>
                <Badge tone="slate">{c.document_count} docs</Badge>
              </div>
              <p className="mt-4 font-semibold">{c.title}</p>
              <p className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                <CalendarClock className="h-3.5 w-3.5" aria-hidden /> {c.due_date ? `Due ${formatDate(c.due_date)}` : "No due date"}
              </p>
            </Link>
          ))}
        </div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="New collection">
        <CollectionForm
          initial={EMPTY}
          submitLabel="Create"
          onSubmit={async (d) => {
            const c = await api.post<Collection>("/api/collections", toPayload(d));
            navigate(`/collections/${c.id}`);
          }}
        />
      </Modal>
    </div>
  );
}

export function CollectionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [col, setCol] = useState<Collection | null>(null);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [edit, setEdit] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, d] = await Promise.all([api.get<Collection>(`/api/collections/${id}`), api.get<{ items: DocumentItem[] }>(`/api/documents?collection_id=${id}&limit=200`)]);
      setCol(c);
      setDocs(d.items);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not load.", "error");
      navigate("/collections");
    }
  }, [id, navigate, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh while analyses are running.
  useEffect(() => {
    if (!docs.some((d) => d.status === "queued" || d.status === "analyzing")) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [docs, load]);

  if (!col) return <PageLoader />;
  return (
    <div className="space-y-6">
      <div>
        <Link to="/collections" className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Collections
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{col.title}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {col.due_date ? `Due ${formatDate(col.due_date)}` : "No due date"} · min. match {col.min_match_words} words · quotes {col.exclude_quotes ? "not counted" : "counted"} · references{" "}
              {col.exclude_bibliography ? "not counted" : "counted"}
            </p>
          </div>
          <div className="flex gap-2">
            <Button icon={<Upload className="h-4 w-4" />} onClick={() => navigate(`/?collection=${col.id}`)}>
              Add document
            </Button>
            <Button variant="secondary" onClick={() => setEdit(true)}>
              Edit
            </Button>
            <Button variant="ghost" aria-label="Delete collection" onClick={() => setConfirm(true)}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
        {col.instructions && <p className="mt-4 whitespace-pre-wrap rounded-2xl bg-white p-4 text-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">{col.instructions}</p>}
      </div>
      <Card className="overflow-hidden">
        {docs.length === 0 ? (
          <EmptyState icon={<Upload className="h-6 w-6" />} title="No documents yet" action={<Button onClick={() => navigate(`/?collection=${col.id}`)}>Add the first document</Button>}>
            Each document is compared with external sources, your library and every other document here (except earlier drafts by the same author).
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {docs.map((d) => (
              <DocumentRow key={d.id} d={d} />
            ))}
          </ul>
        )}
      </Card>
      <p className="text-xs text-slate-500">Tip: after new documents arrive, re-run older reports so they are compared with the newer submissions too.</p>

      <Modal open={edit} onClose={() => setEdit(false)} title="Edit collection">
        <CollectionForm
          initial={{ ...col, due_date: toLocalInput(col.due_date) }}
          submitLabel="Save"
          onSubmit={async (d) => {
            setCol(await api.put<Collection>(`/api/collections/${col.id}`, toPayload(d)));
            setEdit(false);
            toast("Saved.", "success");
          }}
        />
      </Modal>
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Delete collection?">
        <p className="text-sm text-slate-600 dark:text-slate-300">This permanently deletes the collection and all {docs.length} documents in it.</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              await api.del(`/api/collections/${col.id}`);
              toast("Collection deleted.", "success");
              navigate("/collections");
            }}
          >
            Delete everything
          </Button>
        </div>
      </Modal>
    </div>
  );
}
