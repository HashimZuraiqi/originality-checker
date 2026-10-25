import { Activity, Database, KeyRound, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, Field, Input, Modal, SectionTitle, Select, Toggle } from "../components/ui";
import { api, type Preferences, type SystemStatus } from "../lib/api";
import { useAuth, useTheme, useToast } from "../lib/app-state";
import { formatDate } from "../lib/format";

type ActivityRow = { action: string; target: string; ip: string; detail: string; at: string };

export default function Settings() {
  const { me, setPreferences, refresh } = useAuth();
  const { theme, setTheme } = useTheme();
  const toast = useToast();
  const navigate = useNavigate();
  const [prefs, setPrefs] = useState<Preferences | null>(me?.preferences ?? null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  useEffect(() => {
    api.get<SystemStatus>("/api/system/status").then(setStatus).catch(() => {});
    api.get<ActivityRow[]>("/api/account/activity").then(setActivity).catch(() => {});
  }, []);

  const save = async (patch: Partial<Preferences>) => {
    if (!prefs) return;
    const optimistic = { ...prefs, ...patch };
    setPrefs(optimistic);
    try {
      const r = await api.put<{ preferences: Preferences }>("/api/settings", patch);
      setPrefs(r.preferences);
      setPreferences(r.preferences);
      toast("Settings saved.", "success");
    } catch (e) {
      setPrefs(prefs);
      toast(e instanceof Error ? e.message : "Could not save.", "error");
    }
  };

  if (!prefs || !me) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <SectionTitle title="Settings" subtitle={`Signed in as ${me.user.name} (${me.user.email})`} />

      <Card className="space-y-5 p-6">
        <h2 className="flex items-center gap-2 font-semibold">
          <ShieldCheck className="h-5 w-5 text-brand-500" aria-hidden /> Privacy
        </h2>
        <Toggle
          checked={prefs.external_search}
          onChange={(v) => save({ external_search: v })}
          label="Search external sources"
          description="Sends short excerpts (about 10-word phrases, keywords, reference titles) to OpenAlex, Crossref, Semantic Scholar, arXiv, Wikipedia and any configured web search API. Your full document is never sent. Off: only your library and collections are compared."
        />
        <Toggle
          checked={prefs.llm_assist}
          onChange={(v) => save({ llm_assist: v })}
          label="Allow the AI writing assistant"
          description="When you ask for a rewrite, the selected text is sent to Anthropic's API. Anthropic does not train on API data by default. Off: only the on-server rule-based editor is used."
        />
        <Toggle checked={prefs.peer_comparison} onChange={(v) => save({ peer_comparison: v })} label="Compare documents within a collection" description="Detects overlap between submissions in the same collection." />
        <Field label="Keep documents for" htmlFor="retention" hint="Documents, reports and files older than this are deleted automatically.">
          <Select id="retention" value={String(prefs.retention_days)} onChange={(e) => save({ retention_days: Number(e.target.value) })} className="sm:w-60">
            {[7, 30, 90, 180, 365, 730, 3650].map((d) => (
              <option key={d} value={d}>
                {d === 3650 ? "10 years" : d >= 365 ? `${d / 365} year${d > 365 ? "s" : ""}` : `${d} days`}
              </option>
            ))}
          </Select>
        </Field>
      </Card>

      <Card className="space-y-4 p-6">
        <h2 className="font-semibold">Appearance</h2>
        <Field label="Theme" htmlFor="theme">
          <Select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as "light" | "dark" | "system")} className="sm:w-60">
            <option value="system">Match system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </Select>
        </Field>
      </Card>

      <Card className="space-y-4 p-6">
        <h2 className="flex items-center gap-2 font-semibold">
          <KeyRound className="h-5 w-5 text-brand-500" aria-hidden /> Password
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Current password" htmlFor="pw-cur">
            <Input id="pw-cur" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
          </Field>
          <Field label="New password" htmlFor="pw-new" hint="10+ characters, mixed case and a number.">
            <Input id="pw-new" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
          </Field>
        </div>
        <div className="flex justify-end">
          <Button
            loading={busy === "pw"}
            disabled={!pw.current || !pw.next}
            onClick={async () => {
              setBusy("pw");
              try {
                const form = new FormData();
                form.append("current_password", pw.current);
                form.append("new_password", pw.next);
                await api.post("/api/account/password", form);
                setPw({ current: "", next: "" });
                toast("Password changed.", "success");
              } catch (e) {
                toast(e instanceof Error ? e.message : "Could not change password.", "error");
              } finally {
                setBusy(null);
              }
            }}
          >
            Change password
          </Button>
        </div>
      </Card>

      {status && (
        <Card className="p-6">
          <h2 className="flex items-center gap-2 font-semibold">
            <Database className="h-5 w-5 text-brand-500" aria-hidden /> System capabilities
          </h2>
          <p className="mt-1 text-sm text-slate-500">What this server can search and which models it uses.</p>
          <ul className="mt-4 divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {status.providers.map((p) => (
              <li key={p.name} className="flex items-center justify-between gap-3 py-2">
                <span>{p.label}</span>
                <Badge tone={p.enabled && p.configured ? "emerald" : "slate"}>{!p.enabled ? "Disabled" : p.configured ? "Available" : "Needs API key"}</Badge>
              </li>
            ))}
            <li className="flex items-center justify-between gap-3 py-2">
              <span>Semantic model: {status.embedding.label}</span>
              <Badge tone={status.embedding.degraded ? "amber" : "emerald"}>{status.embedding.degraded ? "Degraded" : "Active"}</Badge>
            </li>
            <li className="flex items-center justify-between gap-3 py-2">
              <span>AI writing assistant (Claude)</span>
              <Badge tone={status.assistant_configured ? "emerald" : "slate"}>{status.assistant_configured ? "Configured" : "Not configured"}</Badge>
            </li>
            <li className="flex items-center justify-between gap-3 py-2">
              <span>Grammar engine</span>
              <span className="text-slate-500">{status.grammar_engine}</span>
            </li>
            <li className="flex items-center justify-between gap-3 py-2">
              <span>Limits</span>
              <span className="text-slate-500">
                {status.limits.max_upload_mb} MB · {status.limits.max_document_words.toLocaleString()} words · {status.limits.max_pdf_pages} PDF pages
              </span>
            </li>
          </ul>
        </Card>
      )}

      <Card className="p-6">
        <h2 className="flex items-center gap-2 font-semibold">
          <Activity className="h-5 w-5 text-brand-500" aria-hidden /> Recent account activity
        </h2>
        {activity.length ? (
          <ul className="mt-3 max-h-72 divide-y divide-slate-100 overflow-y-auto text-sm dark:divide-slate-800">
            {activity.map((a, i) => (
              <li key={i} className="flex justify-between gap-3 py-2">
                <span>
                  {a.action.replace(/_/g, " ")}
                  {a.detail && <span className="text-slate-500"> · {a.detail}</span>}
                </span>
                <span className="shrink-0 text-xs text-slate-500">
                  {formatDate(a.at)}
                  {a.ip && ` · ${a.ip}`}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-slate-500">No activity recorded yet.</p>
        )}
      </Card>

      <Card className="border-red-200 p-6 dark:border-red-500/30">
        <h2 className="flex items-center gap-2 font-semibold text-red-600 dark:text-red-400">
          <Trash2 className="h-5 w-5" aria-hidden /> Delete account
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">Permanently deletes your account, documents, reports, revisions, collections and library.</p>
        <Button variant="danger" className="mt-4" onClick={() => setConfirmDelete(true)}>
          Delete my account
        </Button>
      </Card>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete your account?">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          This cannot be undone. Type <strong>DELETE</strong> to confirm.
        </p>
        <Input className="mt-3" aria-label="Type DELETE to confirm" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={confirmText !== "DELETE"}
            loading={busy === "delete"}
            onClick={async () => {
              setBusy("delete");
              try {
                await api.del("/api/account");
                await refresh();
                navigate("/");
              } catch (e) {
                toast(e instanceof Error ? e.message : "Could not delete.", "error");
                setBusy(null);
              }
            }}
          >
            Delete everything
          </Button>
        </div>
      </Modal>
    </div>
  );
}
