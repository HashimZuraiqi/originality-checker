import { BookCheck, Bot, Globe2, Lock, PenLine, ScanSearch, Sparkles } from "lucide-react";
import { useState } from "react";
import { AuthForm } from "../components/AuthForm";
import { Dropzone } from "../components/Dropzone";
import { Logo, ThemeSwitch } from "../components/Layout";
import { Button, Card, Modal, Textarea } from "../components/ui";
import { usePendingUpload } from "../lib/app-state";

const FEATURES = [
  { icon: Globe2, title: "Finds sources automatically", text: "Searches OpenAlex, Crossref, Semantic Scholar, arXiv, Wikipedia and (optionally) the open web. You never collect comparison documents yourself." },
  { icon: ScanSearch, title: "Exact and paraphrased overlap", text: "Fingerprint matching catches copied wording; sentence embeddings catch close paraphrase. Every match links to its real source." },
  { icon: BookCheck, title: "Citation-aware", text: "Distinguishes quoted-and-cited text, reference lists and common phrasing from unattributed overlap, and checks references against Crossref." },
  { icon: Bot, title: "Honest AI-writing indicators", text: "Explains which style signals were found, with an inconclusive band and clear caveats instead of a misleading percentage." },
  { icon: PenLine, title: "Writing studio", text: "Rewrite passages in six styles with side-by-side changes you accept or reject one by one. Citations and quotations stay protected." },
  { icon: Lock, title: "Private by design", text: "Documents are encrypted at rest, never shared or used for training, and deleted on your retention schedule." },
];

export default function Landing() {
  const { setPending } = usePendingUpload();
  const [authOpen, setAuthOpen] = useState(false);
  const [mode, setMode] = useState<"signin" | "signup">("signup");
  const [paste, setPaste] = useState("");

  const start = (p: { file?: File; text?: string }) => {
    setPending(p);
    setMode("signup");
    setAuthOpen(true);
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-brand-100/60 via-slate-50 to-slate-50 dark:from-brand-950/60 dark:via-slate-950 dark:to-slate-950">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Logo />
        <div className="flex items-center gap-2">
          <ThemeSwitch />
          <Button
            variant="ghost"
            onClick={() => {
              setMode("signin");
              setAuthOpen(true);
            }}
          >
            Sign in
          </Button>
          <Button
            className="hidden sm:inline-flex"
            onClick={() => {
              setMode("signup");
              setAuthOpen(true);
            }}
          >
            Create account
          </Button>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-6xl px-4 pb-20 pt-10 sm:px-6 sm:pt-16">
        <section className="mx-auto max-w-3xl text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-brand-700 shadow-sm ring-1 ring-brand-100 dark:bg-slate-900 dark:text-brand-300 dark:ring-slate-800">
            <Sparkles className="h-3.5 w-3.5" aria-hidden /> One upload. A complete, explainable integrity report.
          </span>
          <h1 className="mt-6 text-4xl font-extrabold tracking-tight sm:text-6xl">
            Check originality.
            <br />
            <span className="bg-gradient-to-r from-brand-600 to-violet-600 bg-clip-text text-transparent dark:from-brand-400 dark:to-violet-400">Improve the writing.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-slate-600 dark:text-slate-300">
            Upload an essay, thesis chapter or paper. We search scholarly and open sources for you, show every overlap with its evidence, review citations,
            explain AI-writing indicators honestly, and help you revise.
          </p>
        </section>

        <Card className="mx-auto mt-10 max-w-3xl p-4 sm:p-6">
          <Dropzone onFile={(f) => start({ file: f })} />
          <div className="my-4 flex items-center gap-3 text-xs font-medium uppercase tracking-wider text-slate-400">
            <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" /> or paste text <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
          </div>
          <Textarea aria-label="Paste text to check" placeholder="Paste at least a paragraph of text…" value={paste} onChange={(e) => setPaste(e.target.value)} className="min-h-[110px]" />
          <div className="mt-3 flex justify-end">
            <Button size="lg" disabled={paste.trim().split(/\s+/).length < 20} onClick={() => start({ text: paste })}>
              Check text
            </Button>
          </div>
        </Card>

        <section className="mt-20 grid gap-5 sm:grid-cols-2 lg:grid-cols-3" aria-label="Features">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <Card key={title} className="p-6">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
                <Icon className="h-5 w-5" aria-hidden />
              </div>
              <h2 className="font-semibold">{title}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{text}</p>
            </Card>
          ))}
        </section>

        <section className="mx-auto mt-20 max-w-3xl">
          <h2 className="text-center text-2xl font-bold tracking-tight">How it works</h2>
          <ol className="mt-8 space-y-4">
            {[
              ["Upload or paste", "PDF, Word or text. Nothing else to prepare."],
              ["Automatic analysis", "Text extraction, citation parsing, source discovery, exact and semantic comparison, AI-writing indicators and writing checks, with live progress for each stage."],
              ["Review the evidence", "Every highlight opens the matched source passage, the score, why it was flagged and what to do next."],
              ["Improve and export", "Revise in the writing studio, then download the report and revised document as PDF or Word."],
            ].map(([t, d], i) => (
              <li key={t} className="flex gap-4 rounded-2xl bg-white/70 p-4 ring-1 ring-slate-200/70 dark:bg-slate-900/50 dark:ring-slate-800">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white">{i + 1}</span>
                <div>
                  <p className="font-semibold">{t}</p>
                  <p className="text-sm text-slate-600 dark:text-slate-400">{d}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="mt-8 text-center text-xs text-slate-500">
            Similarity is not proof of plagiarism and AI-writing indicators are not proof of AI use. Results are evidence for a human to review.
          </p>
        </section>
      </main>

      <Modal open={authOpen} onClose={() => setAuthOpen(false)} title={mode === "signup" ? "Create your free account" : "Welcome back"}>
        <p className="mb-5 text-sm text-slate-500">An account keeps your documents private and lets you come back to your reports.</p>
        <AuthForm key={mode} initialMode={mode} onDone={() => setAuthOpen(false)} />
      </Modal>
    </div>
  );
}
