"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { analyze, tokenize, quotedRanges, bibliographyStart } = require("../src/engine");

const WIKI =
  "The mitochondrion is a double membrane bound organelle found in most eukaryotic organisms. " +
  "Mitochondria generate most of the cell's supply of adenosine triphosphate, used as a source of chemical energy.";
const OTHER =
  "Photosynthesis is a process used by plants and other organisms to convert light energy into chemical energy " +
  "that through cellular respiration can later be released to fuel the organism's activities.";

test("tokenize lowercases and keeps character offsets", () => {
  const t = tokenize("Hello, World! It's   fine.");
  assert.deepEqual(t.map((x) => x.word), ["hello", "world", "it's", "fine"]);
  assert.equal("Hello, World! It's   fine.".slice(t[2].start, t[2].end), "It's");
});

test("original text scores 0%", () => {
  const r = analyze("My cat enjoys sleeping in warm sunny spots every afternoon without fail at all.", [
    { id: "a", title: "A", text: WIKI, kind: "library" },
  ]);
  assert.equal(r.similarity, 0);
  assert.equal(r.matches.length, 0);
  assert.equal(r.sources.length, 0);
});

test("a verbatim copy scores 100%", () => {
  const r = analyze(WIKI, [{ id: "a", title: "A", text: WIKI, kind: "library" }]);
  assert.equal(r.similarity, 100);
  assert.equal(r.matchedWords, r.totalWords);
  assert.equal(r.sources[0].sourceId, "a");
  assert.equal(r.matches[0].sourceText, WIKI.slice(0, -1)); // ends at the last word
});

test("matching ignores case and punctuation and reports correct offsets", () => {
  const copied = WIKI.toUpperCase().replace(/[.,]/g, "");
  const intro = "In my essay I will say that ";
  const text = intro + copied + " That is all I have to say about it today.";
  const r = analyze(text, [{ id: "a", title: "A", text: WIKI, kind: "library" }]);
  assert.equal(r.matches.length, 1);
  const m = r.matches[0];
  assert.equal(text.slice(m.start, m.end), m.text);
  assert.ok(m.text.startsWith("THE MITOCHONDRION"));
  assert.ok(r.similarity > 50 && r.similarity < 100);
});

test("each word is attributed to one source and percentages add up", () => {
  const text = WIKI + " " + OTHER + " And finally some words of my very own to finish things off nicely.";
  const r = analyze(text, [
    { id: "w", title: "Wiki", text: WIKI, kind: "library" },
    { id: "o", title: "Other", text: OTHER, kind: "library" },
    { id: "dup", title: "Duplicate of wiki", text: WIKI, kind: "submission" },
  ]);
  const sum = r.sources.reduce((a, s) => a + s.words, 0);
  assert.equal(sum, r.matchedWords);
  // The duplicate source matched the same words but the first claimant wins.
  assert.deepEqual(r.sources.map((s) => s.sourceId).sort(), ["o", "w"]);
  const pctSum = r.sources.reduce((a, s) => a + s.percent, 0);
  assert.ok(Math.abs(pctSum - r.similarity) <= 0.2);
});

test("short overlaps below the minimum match length are ignored", () => {
  const r = analyze("I think the cell's supply of adenosine is interesting to me.", [
    { id: "a", title: "A", text: WIKI, kind: "library" },
  ]);
  assert.equal(r.similarity, 0);
});

test("excludeQuotes skips quoted passages", () => {
  const text = `As the textbook says, "${WIKI}" I agree with this statement completely.`;
  const src = [{ id: "a", title: "A", text: WIKI, kind: "library" }];
  assert.ok(analyze(text, src).similarity > 50);
  const r = analyze(text, src, { excludeQuotes: true });
  assert.equal(r.similarity, 0);
  assert.ok(r.excludedWords > 0);
});

test("excludeBibliography skips a trailing references section only", () => {
  const body = "This is my own analysis of cell biology with plenty of original thoughts and words. ".repeat(4);
  const text = body + "\nReferences\n" + WIKI;
  const src = [{ id: "a", title: "A", text: WIKI, kind: "library" }];
  assert.ok(analyze(text, src).similarity > 0);
  assert.equal(analyze(text, src, { excludeBibliography: true }).similarity, 0);
  assert.equal(bibliographyStart("Sources\n" + body), -1);
});

test("quotedRanges handles straight and curly quotes", () => {
  const t = 'a "b" c “d” e';
  const ranges = quotedRanges(t).map(([s, e]) => t.slice(s, e));
  assert.deepEqual(ranges.sort(), ['"b"', "“d”"]);
});

test("empty text and no sources are safe", () => {
  assert.equal(analyze("", []).similarity, 0);
  assert.equal(analyze("one two", [{ id: "a", title: "A", text: "", kind: "library" }]).similarity, 0);
});
