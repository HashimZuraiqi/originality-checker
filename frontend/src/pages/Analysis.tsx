import { AlertTriangle, CheckCircle2, Circle, Loader2, MinusCircle, RotateCcw, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, Meter, PageLoader } from "../components/ui";
import { api, type Job, type Stage } from "../lib/api";
import { useToast } from "../lib/app-state";

function StageIcon({ status }: { status: Stage["status"] }) {
  if (status === "done") return <CheckCircle2 className="h-5 w-5 text-emerald-500" aria-label="done" />;
  if (status === "running") return <Loader2 className="h-5 w-5 animate-spin text-brand-500" aria-label="in progress" />;
  if (status === "failed") return <XCircle className="h-5 w-5 text-red-500" aria-label="failed" />;
  if (status === "skipped") return <MinusCircle className="h-5 w-5 text-slate-400" aria-label="skipped" />;
  return <Circle className="h-5 w-5 text-slate-300 dark:text-slate-600" aria-label="waiting" />;
}

function elapsed(s: Stage): string {
  if (!s.started_at) return "";
  const end = s.finished_at ? new Date(s.finished_at).getTime() : Date.now();
  const sec = Math.max(0, (end - new Date(s.started_at).getTime()) / 1000);
  return sec < 1 ? "<1 s" : `${sec.toFixed(sec < 10 ? 1 : 0)} s`;
}

export default function Analysis() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number>();

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const j = await api.get<Job>(`/api/jobs/${jobId}`);
        if (!alive) return;
        setJob(j);
        if (j.status === "completed") {
          setTimeout(() => alive && navigate(`/documents/${j.document_id}`, { replace: true }), 600);
          return;
        }
        if (j.status === "failed" || j.status === "cancelled") return;
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Could not load progress.");
        return;
      }
      timer.current = window.setTimeout(poll, 1000);
    };
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer.current);
    };
  }, [jobId, navigate]);

  if (error) {
    return (
      <Card className="mx-auto max-w-xl p-8 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-amber-500" />
        <p className="mt-3 font-semibold">{error}</p>
        <Link to="/history" className="mt-4 inline-block text-sm font-medium text-brand-600 hover:underline">
          Back to history
        </Link>
      </Card>
    );
  }
  if (!job) return <PageLoader />;

  const pct = Math.round(job.progress * 100);
  const active = job.status === "queued" || job.status === "running";

  const retry = async () => {
    try {
      const res = await api.post<{ job: Job }>(`/api/documents/${job.document_id}/analyze`, {});
      navigate(`/analysis/${res.job.id}`, { replace: true });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not restart.", "error");
    }
  };

  return (
    <div className="mx-auto max-w-2xl">
      <Card className="p-6 sm:p-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              {job.status === "completed" ? "Analysis complete" : job.status === "failed" ? "Analysis failed" : job.status === "cancelled" ? "Analysis cancelled" : "Analysing your document"}
            </h1>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400" aria-live="polite">
              {job.status === "queued" ? "Waiting for a free worker…" : active ? "You can leave this page; the analysis continues in the background." : job.error || ""}
            </p>
          </div>
          <span className="text-2xl font-bold tabular-nums text-brand-600 dark:text-brand-400">{pct}%</span>
        </div>
        <div className="mt-5">
          <Meter value={pct} tone={job.status === "failed" ? "red" : "brand"} />
        </div>

        <ol className="mt-7 space-y-1" aria-label="Analysis stages">
          {job.stages.map((s) => (
            <li key={s.key} className={`flex items-start gap-3 rounded-xl px-3 py-2.5 ${s.status === "running" ? "bg-brand-50/70 dark:bg-brand-500/10" : ""}`}>
              <StageIcon status={s.status} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <p className={`text-sm font-medium ${s.status === "pending" ? "text-slate-400 dark:text-slate-500" : ""}`}>{s.label}</p>
                  <span className="text-xs tabular-nums text-slate-400">{elapsed(s)}</span>
                </div>
                {s.detail && <p className={`mt-0.5 text-xs ${s.status === "failed" ? "text-red-600 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}`}>{s.detail}</p>}
              </div>
            </li>
          ))}
        </ol>

        <div className="mt-7 flex flex-wrap justify-end gap-3">
          {active && (
            <Button
              variant="danger"
              loading={cancelling}
              onClick={async () => {
                setCancelling(true);
                try {
                  setJob(await api.post<Job>(`/api/jobs/${job.id}/cancel`));
                } catch (e) {
                  toast(e instanceof Error ? e.message : "Could not cancel.", "error");
                } finally {
                  setCancelling(false);
                }
              }}
            >
              Cancel analysis
            </Button>
          )}
          {(job.status === "failed" || job.status === "cancelled") && (
            <>
              <Button variant="secondary" onClick={() => navigate("/")}>
                Check another document
              </Button>
              <Button icon={<RotateCcw className="h-4 w-4" />} onClick={retry}>
                Try again
              </Button>
            </>
          )}
          {job.status === "completed" && (
            <Button onClick={() => navigate(`/documents/${job.document_id}`)}>Open report</Button>
          )}
        </div>
      </Card>
    </div>
  );
}
