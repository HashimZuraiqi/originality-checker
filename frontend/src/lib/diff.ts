import type { Change } from "./api";

/** Apply non-overlapping changes whose ids are in `accepted`. Offsets refer to `text`. */
export function applyChanges(text: string, changes: Change[], accepted: Set<string>): string {
  const items = changes.filter((c) => accepted.has(c.id)).sort((a, b) => a.start - b.start);
  let out = "";
  let pos = 0;
  for (const c of items) {
    if (c.start < pos) continue;
    out += text.slice(pos, c.start) + c.replacement;
    pos = c.end;
  }
  return out + text.slice(pos);
}

export type DiffPart = { type: "same" | "add" | "del"; text: string };

/** Word-level diff (LCS). Used to compare saved versions. */
export function wordDiff(a: string, b: string, limit = 6000): DiffPart[] | null {
  const ta = a.match(/\s+|[^\s]+/g) ?? [];
  const tb = b.match(/\s+|[^\s]+/g) ?? [];
  // Trim common prefix/suffix to keep the table small.
  let pre = 0;
  while (pre < ta.length && pre < tb.length && ta[pre] === tb[pre]) pre++;
  let suf = 0;
  while (suf < ta.length - pre && suf < tb.length - pre && ta[ta.length - 1 - suf] === tb[tb.length - 1 - suf]) suf++;
  const A = ta.slice(pre, ta.length - suf);
  const B = tb.slice(pre, tb.length - suf);
  if (A.length * B.length > limit * limit) return null;
  const dp: Uint32Array[] = Array.from({ length: A.length + 1 }, () => new Uint32Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const parts: DiffPart[] = [];
  const push = (type: DiffPart["type"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };
  if (pre) push("same", ta.slice(0, pre).join(""));
  let i = 0;
  let j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) {
      push("same", A[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) push("del", A[i++]);
    else push("add", B[j++]);
  }
  while (i < A.length) push("del", A[i++]);
  while (j < B.length) push("add", B[j++]);
  if (suf) push("same", ta.slice(ta.length - suf).join(""));
  return parts;
}
