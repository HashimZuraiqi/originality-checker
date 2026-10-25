"use strict";

// All DOM is built with createElement/textContent, never innerHTML, so
// student-supplied text can never inject markup.

const app = document.getElementById("app");
const SOURCE_COLORS = [
  "#e5484d", "#2f80ed", "#30a46c", "#f5a524", "#8e4ec6",
  "#12a594", "#d6409f", "#978365", "#3e63dd", "#ef5f00",
];

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch {
    // fall through to the generic message
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status}).`);
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = isError ? "error" : "";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 6000 : 3000);
}

function band(pct) {
  if (pct === 0) return 0;
  if (pct < 25) return 1;
  if (pct < 50) return 2;
  if (pct < 75) return 3;
  return 4;
}

function badge(pct) {
  if (pct === null || pct === undefined) return h("span", { class: "tag", text: "pending" });
  return h("span", { class: `badge band-${band(pct)}`, text: `${pct}%` });
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function setNav(name) {
  for (const a of document.querySelectorAll("[data-nav]")) {
    a.classList.toggle("active", a.dataset.nav === name);
  }
}

// Runs an async action from a button, disabling it meanwhile and showing
// any error as a toast.
async function busy(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Could not read that file."));
    reader.readAsDataURL(file);
  });
}

// File picker that fills `textarea` with the extracted text.
function fileLoader(textarea) {
  const input = h("input", {
    type: "file",
    accept: ".txt,.md,.docx",
    onchange: async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        const base64 = await readFileAsBase64(file);
        const { text, wordCount } = await api("POST", "/api/extract", { filename: file.name, base64 });
        textarea.value = text;
        textarea.dispatchEvent(new Event("input"));
        toast(`Loaded ${wordCount} words from ${file.name}.`);
      } catch (err) {
        toast(err.message, true);
      } finally {
        input.value = "";
      }
    },
  });
  return h("div", {}, h("label", { text: "Or upload a file (.docx, .txt, .md)" }), input);
}

function wordCounter(textarea) {
  const out = h("div", { class: "hint", text: "0 words" });
  const update = () => {
    const n = (textarea.value.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length;
    out.textContent = `${n} word${n === 1 ? "" : "s"}`;
  };
  textarea.addEventListener("input", update);
  update();
  return out;
}

/* ---------- Assignments ---------- */

async function viewAssignments() {
  setNav("assignments");
  const assignments = await api("GET", "/api/assignments");

  const title = h("input", { type: "text", required: true, maxlength: "200" });
  const instructions = h("textarea", { maxlength: "5000" });
  instructions.style.minHeight = "90px";
  const due = h("input", { type: "datetime-local" });
  const minWords = h("input", { type: "number", min: "5", max: "100", value: "8" });
  const exQuotes = h("input", { type: "checkbox" });
  const exBib = h("input", { type: "checkbox" });
  const create = h("button", { type: "submit", text: "Create assignment" });

  const form = h(
    "form",
    {
      class: "panel",
      onsubmit: (e) => {
        e.preventDefault();
        busy(create, async () => {
          const a = await api("POST", "/api/assignments", {
            title: title.value,
            instructions: instructions.value,
            dueDate: due.value ? new Date(due.value).toISOString() : "",
            minMatchWords: minWords.value,
            excludeQuotes: exQuotes.checked,
            excludeBibliography: exBib.checked,
          });
          toast("Assignment created.");
          location.hash = `#/assignment/${a.id}`;
        });
      },
    },
    h("h2", { text: "New assignment" }),
    h("label", { text: "Title" }), title,
    h("label", { text: "Instructions (optional)" }), instructions,
    h("div", { class: "grid2" },
      h("div", {}, h("label", { text: "Due date (optional)" }), due),
      h("div", {}, h("label", { text: "Minimum match length (words)" }), minWords,
        h("div", { class: "hint", text: "Shorter overlaps are ignored as common phrases." })),
    ),
    h("label", { class: "check" }, exQuotes, "Exclude quoted material"),
    h("label", { class: "check" }, exBib, "Exclude bibliography / references section"),
    h("div", { class: "actions" }, create),
  );

  const rows = assignments.map((a) =>
    h("tr", {},
      h("td", {}, h("a", { href: `#/assignment/${a.id}`, text: a.title })),
      h("td", { text: fmtDate(a.dueDate) }),
      h("td", { text: String(a.submissionCount) }),
    ),
  );

  app.replaceChildren(
    h("h1", { text: "Assignments" }),
    h("p", { class: "muted", text: "Create an assignment, collect papers, and review similarity reports." }),
    h("div", { class: "panel" },
      assignments.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", { text: "Title" }), h("th", { text: "Due" }), h("th", { text: "Submissions" }))),
            h("tbody", {}, rows)))
        : h("div", { class: "empty", text: "No assignments yet. Create one below." }),
    ),
    form,
  );
}

async function viewAssignment(id) {
  setNav("assignments");
  const [a, subs] = await Promise.all([
    api("GET", `/api/assignments/${id}`),
    api("GET", `/api/submissions?assignmentId=${encodeURIComponent(id)}`),
  ]);

  const student = h("input", { type: "text", required: true, maxlength: "200" });
  const paperTitle = h("input", { type: "text", required: true, maxlength: "200" });
  const text = h("textarea", { required: true, placeholder: "Paste the paper here…" });
  const submit = h("button", { type: "submit", text: "Submit paper" });

  const form = h(
    "form",
    {
      class: "panel",
      onsubmit: (e) => {
        e.preventDefault();
        busy(submit, async () => {
          const s = await api("POST", "/api/submissions", {
            assignmentId: id,
            studentName: student.value,
            title: paperTitle.value,
            text: text.value,
          });
          toast(`Submitted. Similarity: ${s.report.similarity}%`);
          location.hash = `#/submission/${s.id}`;
        });
      },
    },
    h("h2", { text: "Turn in a paper" }),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { text: "Student name" }), student),
      h("div", {}, h("label", { text: "Paper title" }), paperTitle),
    ),
    fileLoader(text),
    h("label", { text: "Paper text" }), text, wordCounter(text),
    h("div", { class: "actions" }, submit),
  );

  const del = h("button", {
    class: "danger",
    type: "button",
    text: "Delete assignment",
    onclick: () => {
      if (!confirm(`Delete "${a.title}" and all ${subs.length} of its submissions? This cannot be undone.`)) return;
      busy(del, async () => {
        await api("DELETE", `/api/assignments/${id}`);
        toast("Assignment deleted.");
        location.hash = "#/";
      });
    },
  });

  const sorted = [...subs].sort((x, y) => y.submittedAt.localeCompare(x.submittedAt));
  const rows = sorted.map((s) =>
    h("tr", {},
      h("td", { text: s.studentName }),
      h("td", {}, h("a", { href: `#/submission/${s.id}`, text: s.title })),
      h("td", {}, fmtDate(s.submittedAt), " ", s.late ? h("span", { class: "tag late", text: "late" }) : null),
      h("td", { text: String(s.wordCount) }),
      h("td", {}, badge(s.similarity)),
    ),
  );

  const settings = [
    `Due: ${fmtDate(a.dueDate)}`,
    `Min. match: ${a.minMatchWords} words`,
    a.excludeQuotes ? "Quotes excluded" : "Quotes included",
    a.excludeBibliography ? "Bibliography excluded" : "Bibliography included",
  ].join(" · ");

  app.replaceChildren(
    h("div", { class: "crumbs" }, h("a", { href: "#/", text: "Assignments" }), " / "),
    h("h1", { text: a.title }),
    h("p", { class: "muted", text: settings }),
    a.instructions ? h("div", { class: "panel" }, h("h2", { text: "Instructions" }), h("div", { class: "paper", text: a.instructions })) : null,
    h("div", { class: "panel" },
      h("h2", { text: `Submissions (${subs.length})` }),
      subs.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {},
              h("th", { text: "Student" }), h("th", { text: "Title" }), h("th", { text: "Submitted" }),
              h("th", { text: "Words" }), h("th", { text: "Similarity" }))),
            h("tbody", {}, rows)))
        : h("div", { class: "empty", text: "No papers turned in yet." }),
    ),
    form,
    h("div", { class: "actions no-print" }, del),
  );
}

/* ---------- Similarity report ---------- */

async function viewSubmission(id) {
  setNav("assignments");
  const s = await api("GET", `/api/submissions/${id}`);
  let a = null;
  try {
    a = await api("GET", `/api/assignments/${s.assignmentId}`);
  } catch {
    // assignment was deleted; the report still renders
  }
  renderReport(s, a);
}

function renderReport(s, a) {
  const r = s.report;
  const colorOf = new Map(r.sources.map((src, i) => [src.sourceId, SOURCE_COLORS[i % SOURCE_COLORS.length]]));
  const rankOf = new Map(r.sources.map((src) => [src.sourceId, src.rank]));
  let selected = null;

  const compare = h("div", { class: "compare" },
    h("p", { class: "muted", text: "Click a highlighted passage or a source to compare." }));

  // Paper with highlighted matches.
  const paper = h("div", { class: "paper" });
  const marks = [];
  let pos = 0;
  for (const m of r.matches) {
    if (m.start > pos) paper.append(s.text.slice(pos, m.start));
    const color = colorOf.get(m.sourceId);
    const mark = h("mark", {
      title: `Source ${rankOf.get(m.sourceId)} · ${m.words} words`,
      onclick: () => showMatch(m, mark),
    }, s.text.slice(m.start, m.end));
    mark.style.background = color + "40";
    mark.style.borderBottom = `2px solid ${color}`;
    mark.dataset.source = m.sourceId;
    marks.push(mark);
    paper.append(mark);
    pos = m.end;
  }
  if (pos < s.text.length) paper.append(s.text.slice(pos));

  function showMatch(m, mark) {
    for (const x of marks) x.classList.remove("focus");
    mark.classList.add("focus");
    const src = r.sources.find((x) => x.sourceId === m.sourceId);
    compare.replaceChildren(
      h("h2", { text: "Match details" }),
      h("div", { class: "source-kind", text: `Source ${src.rank} · ${src.kind === "library" ? "Library source" : "Student paper"}` }),
      h("div", { class: "source-title", text: src.title }),
      h("p", { class: "muted", text: `${m.words} matching words` }),
      h("strong", { text: "In this paper" }),
      h("blockquote", { text: m.text }),
      h("strong", { text: "In the source" }),
      h("blockquote", { text: m.sourceText || "(Matched words are spread across the source, so no single excerpt is shown.)" }),
    );
  }

  function selectSource(sourceId, li) {
    selected = selected === sourceId ? null : sourceId;
    for (const x of sourceList.children) x.classList.toggle("selected", x === li && selected !== null);
    paper.classList.toggle("filtered", selected !== null);
    for (const mark of marks) mark.classList.toggle("dim", selected !== null && mark.dataset.source !== selected);
    if (selected) {
      const first = marks.find((m) => m.dataset.source === selected);
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  const sourceList = h("ul", { class: "source-list" },
    r.sources.map((src) => {
      const swatch = h("span", { class: "swatch", text: String(src.rank) });
      swatch.style.background = colorOf.get(src.sourceId);
      const li = h("li", { onclick: () => selectSource(src.sourceId, li), title: "Show only this source" },
        swatch,
        h("div", {},
          h("div", { class: "source-title", text: src.title }),
          h("div", { class: "source-kind", text: `${src.kind === "library" ? "Library source" : "Student paper"} · ${src.words} words` })),
        h("strong", { text: `${src.percent}%` }),
      );
      return li;
    }),
  );

  const reanalyze = h("button", {
    class: "secondary",
    type: "button",
    text: "Re-run report",
    onclick: () => busy(reanalyze, async () => {
      const updated = await api("POST", `/api/submissions/${s.id}/reanalyze`);
      toast(`Report updated: ${updated.report.similarity}%`);
      renderReport(updated, a);
    }),
  });
  const del = h("button", {
    class: "danger",
    type: "button",
    text: "Delete submission",
    onclick: () => {
      if (!confirm(`Delete "${s.title}" by ${s.studentName}? This cannot be undone.`)) return;
      busy(del, async () => {
        await api("DELETE", `/api/submissions/${s.id}`);
        toast("Submission deleted.");
        location.hash = a ? `#/assignment/${a.id}` : "#/";
      });
    },
  });

  const scoreEl = h("div", { class: "score", text: `${r.similarity}%` });
  scoreEl.style.color = `var(--band-${band(r.similarity)})`;

  const o = r.options;
  app.replaceChildren(
    h("div", { class: "crumbs" },
      h("a", { href: "#/", text: "Assignments" }), " / ",
      a ? h("a", { href: `#/assignment/${a.id}`, text: a.title }) : "Deleted assignment", " / "),
    h("h1", { text: s.title }),
    h("p", { class: "muted" },
      `${s.studentName} · submitted ${fmtDate(s.submittedAt)} · ${s.wordCount} words `,
      s.late ? h("span", { class: "tag late", text: "late" }) : null),
    h("div", { class: "actions" },
      reanalyze,
      h("button", { class: "secondary", type: "button", text: "Print report", onclick: () => window.print() }),
      del),
    h("div", { class: "report" },
      paper,
      h("aside", { class: "sidebar" },
        h("div", { class: "panel score-card" },
          h("div", { class: "muted", text: "Similarity index" }),
          scoreEl,
          h("div", { class: "stats" },
            h("div", {}, h("div", { class: "muted", text: "Matched words" }), String(r.matchedWords)),
            h("div", {}, h("div", { class: "muted", text: "Total words" }), String(r.totalWords)),
            h("div", {}, h("div", { class: "muted", text: "Excluded words" }), String(r.excludedWords)),
            h("div", {}, h("div", { class: "muted", text: "Documents compared" }), String(r.comparedAgainst)),
          ),
          h("p", { class: "hint" },
            `Min. match ${o.minMatchWords} words · quotes ${o.excludeQuotes ? "excluded" : "included"} · bibliography ${o.excludeBibliography ? "excluded" : "included"} · generated ${fmtDate(r.generatedAt)}`),
        ),
        h("div", { class: "panel" },
          h("h2", { text: `Match overview (${r.sources.length})` }),
          r.sources.length ? sourceList : h("div", { class: "empty", text: "No matching sources found." }),
        ),
        h("div", { class: "panel" }, compare),
      ),
    ),
    h("p", { class: "hint", text: "A similarity index shows overlapping text, not plagiarism by itself. Review each match in context — properly quoted and cited material is expected." }),
  );
}

/* ---------- Source library ---------- */

async function viewSources() {
  setNav("sources");
  const sources = await api("GET", "/api/sources");

  const title = h("input", { type: "text", required: true, maxlength: "200" });
  const url = h("input", { type: "url", placeholder: "https://… (optional)", maxlength: "2000" });
  const text = h("textarea", { required: true, placeholder: "Paste the source text here…" });
  const add = h("button", { type: "submit", text: "Add to library" });

  const form = h("form", {
    class: "panel",
    onsubmit: (e) => {
      e.preventDefault();
      busy(add, async () => {
        await api("POST", "/api/sources", { title: title.value, url: url.value, text: text.value });
        toast("Source added. Re-run existing reports to include it.");
        viewSources();
      });
    },
  },
    h("h2", { text: "Add a source" }),
    h("p", { class: "muted", text: "Papers are checked against every source here and every other submitted paper." }),
    h("div", { class: "grid2" },
      h("div", {}, h("label", { text: "Title" }), title),
      h("div", {}, h("label", { text: "URL" }), url),
    ),
    fileLoader(text),
    h("label", { text: "Source text" }), text, wordCounter(text),
    h("div", { class: "actions" }, add),
  );

  const rows = sources.map((src) => {
    const del = h("button", {
      class: "danger",
      type: "button",
      text: "Delete",
      onclick: () => {
        if (!confirm(`Delete "${src.title}" from the library?`)) return;
        busy(del, async () => {
          await api("DELETE", `/api/sources/${src.id}`);
          toast("Source deleted.");
          viewSources();
        });
      },
    });
    const safeUrl = /^https?:\/\//i.test(src.url || "") ? src.url : null;
    return h("tr", {},
      h("td", {}, src.title, safeUrl ? h("div", {}, h("a", { href: safeUrl, target: "_blank", rel: "noopener noreferrer", text: safeUrl })) : null),
      h("td", { text: String(src.wordCount) }),
      h("td", { text: fmtDate(src.createdAt) }),
      h("td", {}, del),
    );
  });

  app.replaceChildren(
    h("h1", { text: "Source library" }),
    h("div", { class: "panel" },
      sources.length
        ? h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, h("th", { text: "Title" }), h("th", { text: "Words" }), h("th", { text: "Added" }), h("th", {}))),
            h("tbody", {}, rows)))
        : h("div", { class: "empty", text: "The library is empty." }),
    ),
    form,
  );
}

/* ---------- Router ---------- */

async function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  let m;
  try {
    if (hash === "/") await viewAssignments();
    else if (hash === "/sources") await viewSources();
    else if ((m = hash.match(/^\/assignment\/([\w-]+)$/))) await viewAssignment(m[1]);
    else if ((m = hash.match(/^\/submission\/([\w-]+)$/))) await viewSubmission(m[1]);
    else location.hash = "#/";
  } catch (err) {
    app.replaceChildren(
      h("div", { class: "panel" },
        h("h2", { text: "Couldn't load this page" }),
        h("p", { class: "muted", text: err.message }),
        h("a", { href: "#/", text: "Back to assignments" })),
    );
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
route();
