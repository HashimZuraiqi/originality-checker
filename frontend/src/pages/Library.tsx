import { BookOpen, ExternalLink, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Dropzone } from "../components/Dropzone";
import { Button, Card, EmptyState, Field, Input, Modal, PageLoader, SectionTitle, Tabs, Textarea } from "../components/ui";
import { api, type LibrarySource } from "../lib/api";
import { useToast } from "../lib/app-state";
import { formatDate } from "../lib/format";

export default function Library() {
  const toast = useToast();
  const [items, setItems] = useState<LibrarySource[] | null>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"file" | "text">("file");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<LibrarySource[]>("/api/library").then(setItems).catch(() => setItems([]));
  useEffect(() => {
    void load();
  }, []);

  const reset = () => {
    setTitle("");
    setUrl("");
    setText("");
    setFile(null);
  };

  const add = async () => {
    setBusy(true);
    try {
      if (tab === "file") {
        if (!file) throw new Error("Choose a file.");
        const form = new FormData();
        form.append("file", file);
        form.append("title", title);
        form.append("url", url);
        await api.post("/api/library/upload", form);
      } else {
        await api.post("/api/library", { title: title || "Untitled source", url, text });
      }
      toast("Added to your library.", "success");
      setOpen(false);
      reset();
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not add.", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <SectionTitle
        title="Source library"
        subtitle="Private reference documents (course readings, past papers, unpublished sources) that every check also compares against."
        action={
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>
            Add source
          </Button>
        }
      />
      <Card className="overflow-hidden">
        {items === null ? (
          <PageLoader />
        ) : items.length === 0 ? (
          <EmptyState icon={<BookOpen className="h-6 w-6" />} title="Your library is empty" action={<Button onClick={() => setOpen(true)}>Add a source</Button>}>
            Optional. Public sources are found automatically; add material here that search engines cannot see.
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {items.map((s) => (
              <li key={s.id} className="flex items-center gap-3 p-4">
                <BookOpen className="h-5 w-5 shrink-0 text-slate-400" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{s.title}</p>
                  <p className="text-xs text-slate-500">
                    {s.word_count.toLocaleString()} words · added {formatDate(s.created_at)}
                  </p>
                </div>
                {/^https?:\/\//i.test(s.url) && (
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Open source URL">
                    <ExternalLink className="h-4 w-4" />
                  </a>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete ${s.title}`}
                  onClick={async () => {
                    await api.del(`/api/library/${s.id}`);
                    setItems((xs) => xs?.filter((x) => x.id !== s.id) ?? null);
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Add a library source" wide>
        <div className="space-y-4">
          <Tabs tabs={[{ id: "file", label: "Upload file" }, { id: "text", label: "Paste text" }]} value={tab} onChange={setTab} />
          {tab === "file" ? <Dropzone compact file={file} onFile={setFile} /> : <Textarea aria-label="Source text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the source text…" />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Title" htmlFor="lib-title">
              <Input id="lib-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file?.name ?? "Source title"} maxLength={300} />
            </Field>
            <Field label="URL (optional)" htmlFor="lib-url">
              <Input id="lib-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" maxLength={2000} />
            </Field>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button loading={busy} onClick={add}>
              Add to library
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
