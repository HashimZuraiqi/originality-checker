"use strict";

// Similarity engine: word n-gram ("shingle") matching between a submission and
// a set of sources. Every word of the submission ends up either original or
// attributed to exactly one source, so per-source percentages add up to the
// overall similarity index.

const DEFAULT_OPTIONS = {
  ngram: 5, // words per shingle
  minMatchWords: 8, // shortest run of matched words that is reported
  excludeQuotes: false,
  excludeBibliography: false,
};

const WORD_RE = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;
const QUOTE_PAIRS = [
  ['"', '"'],
  ["“", "”"],
  ["«", "»"],
];
const BIBLIOGRAPHY_RE =
  /^[ \t]*(references|bibliography|works cited|sources|citations)[ \t]*:?[ \t]*$/gim;

function normalizeWord(word) {
  return word
    .normalize("NFKC")
    .toLowerCase()
    .replace(/’/g, "'");
}

function tokenize(text) {
  const tokens = [];
  for (const m of text.matchAll(WORD_RE)) {
    tokens.push({ word: normalizeWord(m[0]), start: m.index, end: m.index + m[0].length });
  }
  return tokens;
}

function countWords(text) {
  return tokenize(text).length;
}

// Character ranges [start, end) that sit inside quotation marks.
function quotedRanges(text) {
  const ranges = [];
  for (const [open, close] of QUOTE_PAIRS) {
    let i = 0;
    while (i < text.length) {
      const s = text.indexOf(open, i);
      if (s === -1) break;
      const e = text.indexOf(close, s + 1);
      if (e === -1) break;
      ranges.push([s, e + 1]);
      i = e + 1;
    }
  }
  return ranges;
}

// Character offset where a trailing bibliography starts, or -1. Only a heading
// in the second half of the document counts, so an early "Sources" heading in
// the body does not wipe out the whole paper.
function bibliographyStart(text) {
  let found = -1;
  for (const m of text.matchAll(BIBLIOGRAPHY_RE)) {
    if (m.index >= text.length / 2) {
      found = m.index;
      break;
    }
  }
  return found;
}

function excludedMask(text, tokens, options) {
  const mask = new Array(tokens.length).fill(false);
  if (options.excludeQuotes) {
    const ranges = quotedRanges(text);
    tokens.forEach((t, i) => {
      if (ranges.some(([s, e]) => t.start >= s && t.end <= e)) mask[i] = true;
    });
  }
  if (options.excludeBibliography) {
    const bib = bibliographyStart(text);
    if (bib !== -1) {
      tokens.forEach((t, i) => {
        if (t.start >= bib) mask[i] = true;
      });
    }
  }
  return mask;
}

function shingleKey(tokens, i, n) {
  let key = tokens[i].word;
  for (let j = 1; j < n; j++) key += " " + tokens[i + j].word;
  return key;
}

// Map of shingle -> [{ sourceIndex, tokenIndex }].
function buildIndex(sourceTokens, n) {
  const index = new Map();
  sourceTokens.forEach((tokens, sourceIndex) => {
    for (let i = 0; i + n <= tokens.length; i++) {
      const key = shingleKey(tokens, i, n);
      let hits = index.get(key);
      if (!hits) index.set(key, (hits = []));
      const last = hits[hits.length - 1];
      // Sources are indexed one after another, so this keeps only the first
      // occurrence of a shingle in each source.
      if (!last || last.sourceIndex !== sourceIndex) hits.push({ sourceIndex, tokenIndex: i });
    }
  });
  return index;
}

// Contiguous runs of true values in a boolean array, as [start, end) pairs.
function runs(flags) {
  const out = [];
  let start = -1;
  for (let i = 0; i <= flags.length; i++) {
    if (i < flags.length && flags[i]) {
      if (start === -1) start = i;
    } else if (start !== -1) {
      out.push([start, i]);
      start = -1;
    }
  }
  return out;
}

function round1(x) {
  return Math.round(x * 10) / 10;
}

/**
 * Compare `text` against `sources` ([{ id, title, text, kind }]).
 * Returns a report with an overall similarity percentage, per-source
 * breakdown and the matched passages with character offsets into `text`.
 */
function analyze(text, sources, opts = {}) {
  const options = { ...DEFAULT_OPTIONS, ...opts };
  const n = Math.max(2, Math.floor(options.ngram));
  const minRun = Math.max(n, Math.floor(options.minMatchWords));

  const tokens = tokenize(text);
  const excluded = excludedMask(text, tokens, options);
  const sourceTokens = sources.map((s) => tokenize(s.text));
  const index = buildIndex(sourceTokens, n);

  // matchedBy[s][i] -> source token index that word i matched in source s.
  const matchedBy = sources.map(() => new Map());
  for (let i = 0; i + n <= tokens.length; i++) {
    let skip = false;
    for (let j = i; j < i + n; j++) {
      if (excluded[j]) {
        skip = true;
        break;
      }
    }
    if (skip) continue;
    const hits = index.get(shingleKey(tokens, i, n));
    if (!hits) continue;
    for (const { sourceIndex, tokenIndex } of hits) {
      const m = matchedBy[sourceIndex];
      for (let j = 0; j < n; j++) {
        if (!m.has(i + j)) m.set(i + j, tokenIndex + j);
      }
    }
  }

  // Drop runs shorter than the minimum so stock phrases do not count.
  const candidateRuns = matchedBy.map((m) => {
    const flags = tokens.map((_, i) => m.has(i));
    return runs(flags).filter(([s, e]) => e - s >= minRun);
  });

  // Greedy attribution: the source with the most matched words claims its
  // words first; later sources only get words nobody has claimed yet.
  const order = sources
    .map((_, s) => ({ s, size: candidateRuns[s].reduce((a, [b, e]) => a + e - b, 0) }))
    .filter((x) => x.size > 0)
    .sort((a, b) => b.size - a.size || a.s - b.s)
    .map((x) => x.s);

  const owner = new Array(tokens.length).fill(-1);
  for (const s of order) {
    for (const [start, end] of candidateRuns[s]) {
      for (let i = start; i < end; i++) if (owner[i] === -1) owner[i] = s;
    }
  }

  const total = tokens.length;
  const perSource = new Map();
  const matches = [];
  let i = 0;
  while (i < total) {
    const s = owner[i];
    if (s === -1) {
      i++;
      continue;
    }
    let j = i;
    while (j < total && owner[j] === s) j++;
    const words = j - i;
    // A run trimmed by a bigger source can fall below the minimum; give those
    // leftover words back as original text rather than report a tiny match.
    if (words < minRun) {
      for (let k = i; k < j; k++) owner[k] = -1;
      i = j;
      continue;
    }
    let sFirst = Infinity;
    let sLast = -1;
    for (let k = i; k < j; k++) {
      const t = matchedBy[s].get(k);
      if (t < sFirst) sFirst = t;
      if (t > sLast) sLast = t;
    }
    const st = sourceTokens[s];
    const match = {
      sourceId: sources[s].id,
      start: tokens[i].start,
      end: tokens[j - 1].end,
      words,
      text: text.slice(tokens[i].start, tokens[j - 1].end),
      sourceText: null,
    };
    // Only show a source excerpt when it lines up with the passage; a run
    // stitched from scattered places in the source would be misleading.
    if (sLast - sFirst + 1 <= words * 2) {
      match.sourceText = sources[s].text.slice(st[sFirst].start, st[sLast].end);
    }
    matches.push(match);
    perSource.set(s, (perSource.get(s) || 0) + words);
    i = j;
  }

  const matchedWords = [...perSource.values()].reduce((a, b) => a + b, 0);
  const pct = (w) => (total === 0 ? 0 : round1((w / total) * 100));

  const sourceSummaries = [...perSource.entries()]
    .sort((a, b) => b[1] - a[1] || a[0] - b[0])
    .map(([s, words], rank) => ({
      rank: rank + 1,
      sourceId: sources[s].id,
      title: sources[s].title,
      kind: sources[s].kind,
      words,
      percent: pct(words),
    }));

  return {
    totalWords: total,
    excludedWords: excluded.filter(Boolean).length,
    matchedWords,
    similarity: pct(matchedWords),
    sources: sourceSummaries,
    matches,
    options: {
      ngram: n,
      minMatchWords: minRun,
      excludeQuotes: !!options.excludeQuotes,
      excludeBibliography: !!options.excludeBibliography,
    },
  };
}

module.exports = { analyze, tokenize, countWords, quotedRanges, bibliographyStart, DEFAULT_OPTIONS };
