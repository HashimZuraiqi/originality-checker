// Typed client for the backend API. All requests send the session cookie;
// state-changing requests add the CSRF token returned by /api/auth/me.

export type Preferences = {
  retention_days: number;
  external_search: boolean;
  llm_assist: boolean;
  peer_comparison: boolean;
};

export type User = { id: string; name: string; email: string };
export type Me = { user: User; csrf_token: string; preferences: Preferences };

export type Summary = {
  similarity_index: number;
  unattributed_pct: number;
  semantic_pct: number;
  quoted_pct: number;
  ai_verdict: AiVerdict;
  ai_label: string;
  quality_score: number;
  citation_issues: number;
  source_count: number;
  sources_compared: number;
  coverage_gap: boolean;
};

export type DocumentItem = {
  id: string;
  title: string;
  author_name: string;
  collection_id: string | null;
  filename: string;
  file_type: string;
  size_bytes: number;
  word_count: number;
  language: string | null;
  status: "uploaded" | "queued" | "analyzing" | "completed" | "failed" | "cancelled";
  late: boolean;
  latest_job_id: string | null;
  summary: Summary | null;
  created_at: string;
};

export type Stage = {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  detail: string;
  progress: number;
  started_at?: string;
  finished_at?: string;
};

export type Job = {
  id: string;
  document_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  stages: Stage[];
  progress: number;
  error: string | null;
  attempts: number;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  report_id?: string | null;
};

export type AiVerdict = "insufficient_text" | "few_indicators" | "inconclusive" | "many_indicators";

export type SimilarityFinding = {
  kind: "similarity";
  id: string;
  category: "unattributed" | "cited_no_quotes" | "quoted_uncited" | "quoted_cited" | "bibliography" | "paraphrase" | "paraphrase_cited";
  label: string;
  severity: Severity;
  start: number;
  end: number;
  words: number;
  method: "exact" | "near_exact" | "semantic";
  score: number;
  source_id: string;
  source_excerpt: string | null;
  citation_detected: boolean;
  counts_toward_index: boolean;
  explanation: string;
  next_steps: string;
  evidence: Record<string, unknown> & {
    pairs?: { sentence: string; source_sentence: string; cosine: number; shared_content_words: number; cross_encoder?: number }[];
    strength?: string;
  };
};

export type QualityFinding = {
  kind: "quality";
  id: string;
  category: string;
  severity: Severity;
  start: number;
  end: number;
  message: string;
  suggestion: string | null;
  replacement: string | null;
};

export type CitationFinding = { kind: "citation"; id: string; category: string; severity: Severity; start: number; end: number; message: string };
export type AiFinding = { kind: "ai"; id: string; category: string; severity: Severity; start: number; end: number; message: string };
export type Finding = SimilarityFinding | QualityFinding | CitationFinding | AiFinding;
export type Severity = "high" | "medium" | "low" | "info";

export type SourceRow = {
  id: string;
  rank: number;
  title: string;
  kind: string;
  provider: string;
  text_kind: string;
  url: string | null;
  doi: string | null;
  authors: string[];
  year: number | null;
  license: string | null;
  words: number;
  percent: number;
  semantic_words: number;
  semantic_percent: number;
  matches: number;
  categories: Record<string, number>;
};

export type CoverageProvider = {
  name: string;
  label: string;
  status: string;
  detail?: string;
  queries?: number;
  requests?: number;
  cache_hits?: number;
  results?: number;
  errors?: string[];
};

export type AiFeature = {
  name: string;
  label: string;
  description: string;
  value: number;
  typical_human: number | null;
  typical_ai: number | null;
  contribution: number;
};

export type Report = {
  id: string;
  created_at: string;
  generated_at: string;
  document: {
    id: string;
    title: string;
    author: string;
    words: number;
    language: string | null;
    language_name: string;
    language_confidence: number;
    file_type: string;
    pages: number | null;
    extraction_warnings: string[];
  };
  text: string;
  summary: Summary;
  similarity: {
    scores: Record<string, number>;
    findings: SimilarityFinding[];
    sources: SourceRow[];
    method: {
      shingle_size: number;
      min_match_words: number;
      exclude_quotes: boolean;
      exclude_bibliography: boolean;
      semantic_provider: { name: string; label: string; degraded: boolean; paraphrase_threshold: number; strong_threshold: number } | null;
      reranker: string | null;
      semantic_stats: Record<string, number | boolean>;
      formula: string;
    };
  };
  ai: {
    verdict: AiVerdict;
    label: string;
    explanation?: string;
    score?: number;
    score_note?: string;
    caveats: string[];
    features: AiFeature[];
    paragraphs: { index: number; start: number; end: number; verdict: AiVerdict; score: number; top_indicators: string[] }[];
    markers: { start: number; end: number; text: string }[];
    pushing_up?: string[];
    pushing_down?: string[];
    mixed_signals?: boolean;
    language_supported: boolean;
    model: { version: string; trained_on: string; threshold_low: number; threshold_high: number; cv_metrics: Record<string, number>; limitations: string } | null;
  };
  quality: {
    score: number;
    subscores: Record<string, number>;
    formulas: Record<string, string>;
    readability: Record<string, number>;
    counts: Record<string, number>;
    findings: QualityFinding[];
    engine: string;
  };
  citations: {
    style: string | null;
    citations: { start: number; end: number; text: string; style: string; reference_index: number | null }[];
    references: {
      index: number;
      start: number;
      end: number;
      text: string;
      first_author: string | null;
      year: string | null;
      doi: string | null;
      url: string | null;
      title: string | null;
      cited: boolean;
      verification: { status: string; title?: string; reason?: string; url?: string; doi?: string } | null;
    }[];
    issues: { type: string; start: number; end: number; message: string; severity: Severity }[];
    bibliography_start: number | null;
  };
  findings: { citation: CitationFinding[]; ai: AiFinding[] };
  coverage: {
    external_search: boolean;
    providers: CoverageProvider[];
    summary: string;
    not_covered: string[];
    candidates_found?: number;
    sources_compared?: number;
    full_text_sources?: number;
    duplicates_removed?: number;
    data_shared?: string;
    retrieval_notes?: Record<string, string>;
  };
  queries: { id: string; family: string; text: string; start: number | null; end: number | null }[];
  settings: Record<string, unknown>;
  timings: Record<string, number>;
};

export type Change = {
  id: string;
  start: number;
  end: number;
  original: string;
  replacement: string;
  reason: string;
  kind: "rule" | "ai";
  blocked: boolean;
  warning: string | null;
};

export type RewriteResult = {
  engine: "llm" | "rules";
  model: string | null;
  text: string;
  changes: Change[];
  explanations: string[];
  concerns: string[];
  notice: string | null;
  selection: { start: number; end: number };
};

export type Collection = {
  id: string;
  title: string;
  instructions: string;
  due_date: string | null;
  min_match_words: number;
  exclude_quotes: boolean;
  exclude_bibliography: boolean;
  document_count: number;
  created_at: string;
};

export type LibrarySource = { id: string; title: string; url: string; word_count: number; created_at: string };
export type Revision = { id: string; document_id: string; label: string; word_count: number; created_at: string };

export type SystemStatus = {
  providers: { name: string; label: string; kind: string; enabled: boolean; configured: boolean; requires_key: boolean }[];
  embedding: { name: string; label: string; degraded: boolean };
  assistant_configured: boolean;
  grammar_engine: string;
  limits: { max_upload_mb: number; max_document_words: number; max_pdf_pages: number };
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token;
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      const first = d[0] as { msg?: string; loc?: unknown[] };
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
      return `${field ? field.replace(/_/g, " ") + ": " : ""}${first.msg ?? "Invalid input"}`;
    }
  }
  if (status === 413) return "The upload is too large.";
  if (status >= 500) return "The server had a problem. Please try again.";
  return `Request failed (${status}).`;
}

export async function request<T>(method: string, url: string, body?: unknown, opts: { raw?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection.");
  }
  if (opts.raw) {
    if (!res.ok) {
      let b: unknown = null;
      try {
        b = await res.json();
      } catch {
        /* not JSON */
      }
      throw new ApiError(res.status, messageFrom(b, res.status));
    }
    return res as unknown as T;
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, res.status));
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  del: <T = void>(url: string) => request<T>("DELETE", url),
};

export async function download(method: "GET" | "POST", url: string, body?: unknown, fallbackName = "download") {
  const res = await request<Response>(method, url, body, { raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename="([^"]+)"/.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
