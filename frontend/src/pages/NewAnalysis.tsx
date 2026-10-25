import { ChevronDown, FileUp, Info, Type } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Dropzone } from "../components/Dropzone";
import { Button, Card, Field, Input, Select, Tabs, Textarea, Toggle, cx } from "../components/ui";
import { api, type Collection, type DocumentItem, type Job } from "../lib/api";
import { useAuth, usePendingUpload, useToast } from "../lib/app-state";
import { relativeTime } from "../lib/format";

export default function NewAnalysis() {
  const { me } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { pending, setPending } = usePendingUpload();
  const [tab, setTab] = useState<"file" | "text">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [collectionId, setCollectionId] = useState(params.get("collection") || "");
  const [collections, setCollections] = useState<Collection[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const [minWords, setMinWords] = useState(8);
  const [excludeQuotes, setExcludeQuotes] = useState(true);
  const [excludeBib, setExcludeBib] = useState(true);
  const [external, setExternal] = useState(me?.preferences.external_search ?? true);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<DocumentItem[]>([]);
  const autoStarted = useRef(false);

  useEffect(() => {
    api.get<Collection[]>("/api/collections").then(setCollections).catch(() => {});
    api.get<{ items: DocumentItem[] }>("/api/documents?limit=4").then((r) => setRecent(r.items)).catch(() => {});
  }, []);

  const submit = useCallback(
    async (override?: { file?: File; text?: string }) => {
      const f = override ? override.file : tab === "file" ? file : null;
      const t = override ? override.text : tab === "text" ? text : undefined;
      if (!f && !(t && t.trim())) {
        toast("Choose a file or paste some text first.", "error");
        return;
      }
      const form = new FormData();
      if (f) form.append("file", f);
      else form.append("text", t as string);
      if (title.trim()) form.append("title", title.trim());
      if (author.trim()) form.append("author_name", author.trim());
      if (collectionId) form.append("collection_id", collectionId);
      form.append("options", JSON.stringify({ min_match_words: minWords, exclude_quotes: excludeQuotes, exclude_bibliography: excludeBib, external_search: external }));
      setBusy(true);
      try {
        const res = await api.post<{ document: DocumentItem; job: Job }>("/api/documents", form);
        navigate(`/analysis/${res.job.id}`);
      } catch (e) {
        toast(e instanceof Error ? e.message : "Upload failed.", "error");
        setBusy(false);
      }
    },
    [tab, file, text, title, author, collectionId, minWords, excludeQuotes, excludeBib, external, navigate, toast],
  );

  // A file chosen on the landing page before signing in.
  useEffect(() => {
    if (pending && !autoStarted.current) {
      autoStarted.current = true;
      const p = pending;
      setPending(null);
      if (p.file) setFile(p.file);
      if (p.text) {
        setTab("text");
        setText(p.text);
      }
      void submit(p);
    }
  }, [pending, setPending, submit]);

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Check a document</h1>
        <p className="mt-2 text-slate-500 dark:text-slate-400">Upload or paste your text. Everything else happens automatically.</p>
      </div>

      <Card className="p-4 sm:p-6">
        <div className="mb-4">
          <Tabs
            tabs={[
              { id: "file", label: "Upload file" },
              { id: "text", label: "Paste text" },
            ]}
            value={tab}
            onChange={setTab}
          />
        </div>
        {tab === "file" ? (
          <Dropzone file={file} onFile={setFile} disabled={busy} />
        ) : (
          <div>
            <Textarea aria-label="Text to check" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste your text here…" className="min-h-[240px] font-serif text-[15px]" />
            <p className="mt-1.5 text-right text-xs text-slate-500">{words.toLocaleString()} words</p>
          </div>
        )}

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="Title (optional)" htmlFor="doc-title">
            <Input id="doc-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file ? file.name.replace(/\.[^.]+$/, "") : "Untitled document"} maxLength={300} />
          </Field>
          <Field label="Collection (optional)" htmlFor="doc-col" hint={<Link to="/collections" className="text-brand-600 hover:underline dark:text-brand-400">Manage collections</Link>}>
            <Select id="doc-col" value={collectionId} onChange={(e) => setCollectionId(e.target.value)}>
              <option value="">None</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
          </Field>
          {collectionId && (
            <Field label="Author / student name" htmlFor="doc-author" hint="Used to compare against other submissions in the collection (the same author's drafts are not counted).">
              <Input id="doc-author" value={author} onChange={(e) => setAuthor(e.target.value)} maxLength={200} />
            </Field>
          )}
        </div>

        <button
          type="button"
          onClick={() => setAdvanced(!advanced)}
          className="mt-5 flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
          aria-expanded={advanced}
        >
          <ChevronDown className={cx("h-4 w-4 transition", advanced && "rotate-180")} aria-hidden /> Advanced options
        </button>
        {advanced && (
          <div className="mt-4 space-y-4 rounded-xl bg-slate-50 p-4 dark:bg-slate-950/60">
            <Field label={`Minimum match length: ${minWords} words`} htmlFor="minw" hint="Shorter overlaps are treated as common phrasing and ignored.">
              <input id="minw" type="range" min={5} max={30} value={minWords} onChange={(e) => setMinWords(Number(e.target.value))} className="w-full accent-brand-600" />
            </Field>
            <Toggle checked={excludeQuotes} onChange={setExcludeQuotes} label="Don't count quoted and cited text" description="Quotations with a citation are still shown, but not counted in the similarity index." />
            <Toggle checked={excludeBib} onChange={setExcludeBib} label="Don't count the reference list" />
            <Toggle
              checked={external}
              onChange={setExternal}
              label="Search external sources"
              description="Turn off to compare only against your library and collection."
              disabled={!me?.preferences.external_search}
            />
          </div>
        )}

        <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="flex items-start gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            {external && me?.preferences.external_search
              ? "Short excerpts (about 10-word phrases and keywords) are sent to scholarly search services to find sources. Your document is never shared."
              : "External search is off: nothing leaves this server."}
          </p>
          <Button size="lg" loading={busy} onClick={() => submit()} icon={tab === "file" ? <FileUp className="h-4 w-4" /> : <Type className="h-4 w-4" />}>
            Start check
          </Button>
        </div>
      </Card>

      {recent.length > 0 && (
        <div className="mt-10">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">Recent</h2>
            <Link to="/history" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
              View all
            </Link>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {recent.map((d) => (
              <Link
                key={d.id}
                to={d.status === "completed" ? `/documents/${d.id}` : d.latest_job_id ? `/analysis/${d.latest_job_id}` : `/documents/${d.id}`}
                className="rounded-xl bg-white p-4 ring-1 ring-slate-200 transition hover:shadow-card hover:ring-brand-200 dark:bg-slate-900 dark:ring-slate-800 dark:hover:ring-brand-500/40"
              >
                <p className="truncate font-medium">{d.title}</p>
                <p className="mt-1 text-xs text-slate-500">
                  {relativeTime(d.created_at)} · {d.status === "completed" && d.summary ? `${d.summary.similarity_index}% similarity` : d.status}
                </p>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
