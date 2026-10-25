"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { Store } = require("./store");
const { analyze, countWords } = require("./engine");
const { extractText, ExtractError } = require("./extract");

const MAX_BODY_BYTES = 20 * 1024 * 1024;
const MAX_TEXT_CHARS = 2_000_000;
const PUBLIC_DIR = path.join(__dirname, "..", "public");
const STATIC_FILES = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/index.html": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/styles.css": ["styles.css", "text/css; charset=utf-8"],
};

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function send(res, status, body, type = "application/json; charset=utf-8") {
  const payload = typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": type,
    "Content-Length": Buffer.byteLength(payload),
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
  });
  res.end(payload);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      // Keep draining an oversized body so the client still gets the 413.
      if (size <= MAX_BODY_BYTES) chunks.push(chunk);
    });
    req.on("end", () => {
      if (size > MAX_BODY_BYTES) return reject(new HttpError(413, "Request is too large (limit 20 MB)."));
      if (size === 0) return resolve({});
      try {
        const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          throw new Error("not an object");
        }
        resolve(value);
      } catch {
        reject(new HttpError(400, "Request body must be a JSON object."));
      }
    });
    req.on("error", reject);
  });
}

function str(value, field, { required = true, max = 200 } = {}) {
  if (value === undefined || value === null || value === "") {
    if (required) throw new HttpError(400, `${field} is required.`);
    return "";
  }
  if (typeof value !== "string") throw new HttpError(400, `${field} must be text.`);
  const trimmed = value.trim();
  if (required && !trimmed) throw new HttpError(400, `${field} is required.`);
  if (trimmed.length > max) throw new HttpError(400, `${field} is too long (max ${max} characters).`);
  return trimmed;
}

function documentText(value, field) {
  if (typeof value !== "string") throw new HttpError(400, `${field} is required.`);
  if (value.length > MAX_TEXT_CHARS) throw new HttpError(400, `${field} is too long.`);
  if (countWords(value) === 0) throw new HttpError(400, `${field} must contain at least one word.`);
  return value.replace(/\r\n?/g, "\n");
}

function int(value, field, min, max, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  const n = Number(value);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new HttpError(400, `${field} must be a whole number from ${min} to ${max}.`);
  }
  return n;
}

function sameStudent(a, b) {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

function createApp({ dataFile } = {}) {
  const store = new Store(dataFile);

  function buildReport(submission) {
    const assignment = store.get("assignments", submission.assignmentId);
    const assignmentTitles = new Map(store.list("assignments").map((a) => [a.id, a.title]));
    const sources = [
      ...store.list("sources").map((s) => ({ id: s.id, title: s.title, text: s.text, kind: "library" })),
      ...store
        .list("submissions")
        .filter(
          (other) =>
            other.id !== submission.id &&
            // Resubmissions by the same student to the same assignment are
            // drafts of the same paper, not plagiarism.
            !(other.assignmentId === submission.assignmentId && sameStudent(other.studentName, submission.studentName)),
        )
        .map((other) => ({
          id: other.id,
          title: `Student paper: ${other.studentName} — ${assignmentTitles.get(other.assignmentId) || "deleted assignment"}`,
          text: other.text,
          kind: "submission",
        })),
    ];
    const report = analyze(submission.text, sources, {
      minMatchWords: assignment ? assignment.minMatchWords : undefined,
      excludeQuotes: assignment ? assignment.excludeQuotes : false,
      excludeBibliography: assignment ? assignment.excludeBibliography : false,
    });
    return { ...report, generatedAt: new Date().toISOString(), comparedAgainst: sources.length };
  }

  function summary(sub) {
    return {
      id: sub.id,
      assignmentId: sub.assignmentId,
      studentName: sub.studentName,
      title: sub.title,
      wordCount: sub.wordCount,
      submittedAt: sub.submittedAt,
      late: sub.late,
      similarity: sub.report ? sub.report.similarity : null,
    };
  }

  function assignmentInput(body, existing = {}) {
    const merged = { ...existing, ...body };
    const dueDate = str(merged.dueDate, "Due date", { required: false, max: 40 });
    if (dueDate && Number.isNaN(Date.parse(dueDate))) throw new HttpError(400, "Due date is not a valid date.");
    return {
      title: str(merged.title, "Title"),
      instructions: str(merged.instructions, "Instructions", { required: false, max: 5000 }),
      dueDate: dueDate ? new Date(dueDate).toISOString() : null,
      excludeQuotes: merged.excludeQuotes === true,
      excludeBibliography: merged.excludeBibliography === true,
      minMatchWords: int(merged.minMatchWords, "Minimum match length", 5, 100, 8),
    };
  }

  const routes = [
    ["GET", /^\/api\/assignments$/, () => {
      const counts = new Map();
      for (const s of store.list("submissions")) counts.set(s.assignmentId, (counts.get(s.assignmentId) || 0) + 1);
      return store.list("assignments").map((a) => ({ ...a, submissionCount: counts.get(a.id) || 0 }));
    }],
    ["POST", /^\/api\/assignments$/, async (req) => {
      const input = assignmentInput(await readJson(req));
      return [201, store.insert("assignments", { id: Store.id(), ...input, createdAt: new Date().toISOString() })];
    }],
    ["GET", /^\/api\/assignments\/([\w-]+)$/, (req, [id]) => {
      const a = store.get("assignments", id);
      if (!a) throw new HttpError(404, "Assignment not found.");
      return a;
    }],
    ["PUT", /^\/api\/assignments\/([\w-]+)$/, async (req, [id]) => {
      const a = store.get("assignments", id);
      if (!a) throw new HttpError(404, "Assignment not found.");
      return store.update("assignments", id, assignmentInput(await readJson(req), a));
    }],
    ["DELETE", /^\/api\/assignments\/([\w-]+)$/, (req, [id]) => {
      if (!store.remove("assignments", id)) throw new HttpError(404, "Assignment not found.");
      return [204, null];
    }],

    ["GET", /^\/api\/sources$/, () =>
      store.list("sources").map(({ text, ...rest }) => ({ ...rest, wordCount: countWords(text) }))],
    ["POST", /^\/api\/sources$/, async (req) => {
      const body = await readJson(req);
      const record = {
        id: Store.id(),
        title: str(body.title, "Title"),
        url: str(body.url, "URL", { required: false, max: 2000 }),
        text: documentText(body.text, "Source text"),
        createdAt: new Date().toISOString(),
      };
      return [201, store.insert("sources", record)];
    }],
    ["GET", /^\/api\/sources\/([\w-]+)$/, (req, [id]) => {
      const s = store.get("sources", id);
      if (!s) throw new HttpError(404, "Source not found.");
      return s;
    }],
    ["DELETE", /^\/api\/sources\/([\w-]+)$/, (req, [id]) => {
      if (!store.remove("sources", id)) throw new HttpError(404, "Source not found.");
      return [204, null];
    }],

    ["GET", /^\/api\/submissions$/, (req, _m, url) => {
      const assignmentId = url.searchParams.get("assignmentId");
      return store
        .list("submissions")
        .filter((s) => !assignmentId || s.assignmentId === assignmentId)
        .map(summary);
    }],
    ["POST", /^\/api\/submissions$/, async (req) => {
      const body = await readJson(req);
      const assignmentId = str(body.assignmentId, "Assignment");
      const assignment = store.get("assignments", assignmentId);
      if (!assignment) throw new HttpError(400, "That assignment does not exist.");
      const now = new Date();
      const text = documentText(body.text, "Paper text");
      const submission = {
        id: Store.id(),
        assignmentId,
        studentName: str(body.studentName, "Student name"),
        title: str(body.title, "Paper title"),
        text,
        wordCount: countWords(text),
        submittedAt: now.toISOString(),
        late: assignment.dueDate ? now > new Date(assignment.dueDate) : false,
        report: null,
      };
      submission.report = buildReport(submission);
      return [201, store.insert("submissions", submission)];
    }],
    ["GET", /^\/api\/submissions\/([\w-]+)$/, (req, [id]) => {
      const s = store.get("submissions", id);
      if (!s) throw new HttpError(404, "Submission not found.");
      return s;
    }],
    ["POST", /^\/api\/submissions\/([\w-]+)\/reanalyze$/, (req, [id]) => {
      const s = store.get("submissions", id);
      if (!s) throw new HttpError(404, "Submission not found.");
      return store.update("submissions", id, { report: buildReport(s) });
    }],
    ["DELETE", /^\/api\/submissions\/([\w-]+)$/, (req, [id]) => {
      if (!store.remove("submissions", id)) throw new HttpError(404, "Submission not found.");
      return [204, null];
    }],

    ["POST", /^\/api\/extract$/, async (req) => {
      const body = await readJson(req);
      const filename = str(body.filename, "File name", { max: 255 });
      if (typeof body.base64 !== "string") throw new HttpError(400, "File content is required.");
      const buffer = Buffer.from(body.base64, "base64");
      try {
        const text = extractText(filename, buffer);
        if (countWords(text) === 0) throw new HttpError(400, "No readable text was found in that file.");
        return { text, wordCount: countWords(text) };
      } catch (err) {
        if (err instanceof ExtractError) throw new HttpError(400, err.message);
        throw err;
      }
    }],
  ];

  async function handle(req, res) {
    let url;
    try {
      url = new URL(req.url, "http://localhost");
    } catch {
      return send(res, 400, { error: "Bad request." });
    }
    try {
      if (req.method === "GET" && STATIC_FILES[url.pathname]) {
        const [file, type] = STATIC_FILES[url.pathname];
        return send(res, 200, fs.readFileSync(path.join(PUBLIC_DIR, file)), type);
      }
      let pathMatched = false;
      for (const [method, pattern, handler] of routes) {
        const m = url.pathname.match(pattern);
        if (!m) continue;
        pathMatched = true;
        if (method !== req.method) continue;
        let result = await handler(req, m.slice(1), url);
        let status = 200;
        if (Array.isArray(result) && result.length === 2 && typeof result[0] === "number") {
          [status, result] = result;
        }
        if (status === 204) {
          res.writeHead(204);
          return res.end();
        }
        return send(res, status, result);
      }
      throw new HttpError(pathMatched ? 405 : 404, pathMatched ? "Method not allowed." : "Not found.");
    } catch (err) {
      if (err instanceof HttpError) return send(res, err.status, { error: err.message });
      console.error(err);
      return send(res, 500, { error: "Something went wrong on the server." });
    }
  }

  return { store, handle, server: http.createServer(handle) };
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 3000;
  const host = process.env.HOST || "127.0.0.1";
  const dataFile = process.env.DATA_FILE || path.join(__dirname, "..", "data", "db.json");
  const { server } = createApp({ dataFile });
  server.listen(port, host, () => {
    console.log(`Originality Checker running at http://${host}:${port}`);
    console.log(`Data file: ${dataFile}`);
  });
}

module.exports = { createApp };
