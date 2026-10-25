import type { AiVerdict, Finding, Severity } from "./api";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Band for the similarity index: drives colour AND a text label. */
export function similarityBand(pct: number): { label: string; tone: Tone } {
  if (pct === 0) return { label: "No matches", tone: "emerald" };
  if (pct < 10) return { label: "Low", tone: "emerald" };
  if (pct < 25) return { label: "Moderate", tone: "amber" };
  if (pct < 50) return { label: "High", tone: "orange" };
  return { label: "Very high", tone: "red" };
}

export type Tone = "emerald" | "amber" | "orange" | "red" | "slate" | "violet" | "blue" | "brand";

export const TONE_TEXT: Record<Tone, string> = {
  emerald: "text-emerald-600 dark:text-emerald-400",
  amber: "text-amber-600 dark:text-amber-400",
  orange: "text-orange-600 dark:text-orange-400",
  red: "text-red-600 dark:text-red-400",
  slate: "text-slate-600 dark:text-slate-300",
  violet: "text-violet-600 dark:text-violet-400",
  blue: "text-blue-600 dark:text-blue-400",
  brand: "text-brand-600 dark:text-brand-400",
};

export const TONE_BADGE: Record<Tone, string> = {
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-400/20",
  orange: "bg-orange-50 text-orange-700 ring-orange-600/20 dark:bg-orange-500/10 dark:text-orange-300 dark:ring-orange-400/20",
  red: "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-400/20",
  slate: "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/20",
  violet: "bg-violet-50 text-violet-700 ring-violet-600/20 dark:bg-violet-500/10 dark:text-violet-300 dark:ring-violet-400/20",
  blue: "bg-blue-50 text-blue-700 ring-blue-600/20 dark:bg-blue-500/10 dark:text-blue-300 dark:ring-blue-400/20",
  brand: "bg-brand-50 text-brand-700 ring-brand-600/20 dark:bg-brand-500/10 dark:text-brand-300 dark:ring-brand-400/20",
};

export function aiTone(v: AiVerdict): Tone {
  return v === "many_indicators" ? "violet" : v === "few_indicators" ? "emerald" : "slate";
}

export function qualityTone(score: number): Tone {
  return score >= 85 ? "emerald" : score >= 70 ? "amber" : "orange";
}

export const SEVERITY_LABEL: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low", info: "Info" };
export const SEVERITY_RANK: Record<Severity, number> = { high: 0, medium: 1, low: 2, info: 3 };

/** Visual and textual identity for each finding group. Colour is never the
 * only cue: each group also has an icon name, a label and an underline style. */
export type FindingGroup = "exact" | "paraphrase" | "citation" | "quality" | "ai" | "ok";

export const GROUP_META: Record<FindingGroup, { label: string; tone: Tone; mark: string; underline: string; description: string }> = {
  exact: {
    label: "Copied wording",
    tone: "red",
    mark: "bg-red-100/80 dark:bg-red-500/25",
    underline: "decoration-red-500 decoration-solid",
    description: "Exact or near-exact overlap with a source that needs review.",
  },
  paraphrase: {
    label: "Possible paraphrase",
    tone: "orange",
    mark: "bg-orange-100/80 dark:bg-orange-500/20",
    underline: "decoration-orange-500 decoration-dashed",
    description: "Sentences semantically close to a source.",
  },
  citation: {
    label: "Citation concern",
    tone: "amber",
    mark: "bg-yellow-100 dark:bg-yellow-500/20",
    underline: "decoration-yellow-500 decoration-dotted",
    description: "Missing quotation marks, citation or reference.",
  },
  quality: {
    label: "Writing suggestion",
    tone: "blue",
    mark: "bg-blue-50 dark:bg-blue-500/15",
    underline: "decoration-blue-500 decoration-wavy",
    description: "Grammar, clarity and style suggestions.",
  },
  ai: {
    label: "AI-writing indicators",
    tone: "violet",
    mark: "bg-violet-50 dark:bg-violet-500/15",
    underline: "decoration-violet-500 decoration-double",
    description: "Paragraphs with several AI-associated style indicators (not proof).",
  },
  ok: {
    label: "Properly attributed",
    tone: "slate",
    mark: "bg-slate-100 dark:bg-slate-700/50",
    underline: "decoration-slate-400 decoration-solid",
    description: "Quoted and cited material, cited paraphrase or reference-list overlap.",
  },
};

export function groupOf(f: Finding): FindingGroup {
  if (f.kind === "quality") return "quality";
  if (f.kind === "ai") return "ai";
  if (f.kind === "citation") return "citation";
  switch (f.category) {
    case "unattributed":
      return "exact";
    case "cited_no_quotes":
    case "quoted_uncited":
      return "citation";
    case "paraphrase":
      return "paraphrase";
    default:
      return "ok";
  }
}

export function findingTitle(f: Finding): string {
  if (f.kind === "similarity") return f.label;
  if (f.kind === "quality") return f.category.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  if (f.kind === "citation") return f.category.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  return "AI-writing indicators";
}
