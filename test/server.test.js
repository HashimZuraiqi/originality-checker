"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const { createApp } = require("../src/server");
const { extractText } = require("../src/extract");

const ESSAY =
  "The industrial revolution transformed the economies of Europe and North America during the eighteenth and " +
  "nineteenth centuries, replacing hand production with machines, new chemical processes and steam power.";

// Minimal ZIP writer (one deflated entry per file) for building test .docx files.
function makeZip(files) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const raw = Buffer.from(content, "utf8");
    const data = zlib.deflateRawSync(raw);
    const nameBuf = Buffer.from(name, "utf8");
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(8, 10);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(raw.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, nameBuf, data);
    centrals.push(central, nameBuf);
    offset += 30 + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(Object.keys(files).length, 8);
  eocd.writeUInt16LE(Object.keys(files).length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

const DOCX_XML =
  '<?xml version="1.0"?><w:document><w:body>' +
  '<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world &amp; friends</w:t></w:r></w:p>' +
  "<w:p><w:r><w:t>Second</w:t><w:tab/><w:t>para&#233;</w:t></w:r></w:p>" +
  "</w:body></w:document>";

test("extracts text from .docx and .txt", () => {
  const docx = makeZip({ "[Content_Types].xml": "<Types/>", "word/document.xml": DOCX_XML });
  assert.equal(extractText("paper.DOCX", docx), "Hello world & friends\nSecond\tparaé");
  assert.equal(extractText("a.txt", Buffer.from("﻿plain text")), "plain text");
  assert.throws(() => extractText("a.pdf", Buffer.from("x")), /Unsupported file type/);
  assert.throws(() => extractText("a.docx", Buffer.from("not a zip")), /Not a valid .docx/);
});

async function withServer(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-test-"));
  const dataFile = path.join(dir, "db.json");
  const { server } = createApp({ dataFile });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, url, body) => {
    const res = await fetch(base + url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
    });
    const text = await res.text();
    return { status: res.status, body: text ? JSON.parse(text) : null };
  };
  try {
    await fn(call, { base, dataFile });
  } finally {
    await new Promise((r) => server.close(r));
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("full turn-in flow with similarity reports", async () => {
  await withServer(async (call, { base, dataFile }) => {
    const page = await fetch(base + "/");
    assert.equal(page.status, 200);
    assert.match(await page.text(), /Originality Checker/);

    const a = await call("POST", "/api/assignments", { title: "History essay", minMatchWords: 8 });
    assert.equal(a.status, 201);
    const aid = a.body.id;

    const src = await call("POST", "/api/sources", { title: "Encyclopedia", text: ESSAY });
    assert.equal(src.status, 201);

    // Student 1 copies the encyclopedia.
    const s1 = await call("POST", "/api/submissions", {
      assignmentId: aid, studentName: "Alex", title: "Essay", text: ESSAY,
    });
    assert.equal(s1.status, 201);
    assert.equal(s1.body.report.similarity, 100);
    assert.equal(s1.body.report.sources[0].kind, "library");

    // Student 2 writes something original.
    const original = "I wrote this essay entirely by myself and it talks about my summer holiday at the lake.";
    const s2 = await call("POST", "/api/submissions", {
      assignmentId: aid, studentName: "Sam", title: "Mine", text: original,
    });
    assert.equal(s2.body.report.similarity, 0);

    // Alex resubmits the same text: their own earlier draft is not a match,
    // but the library source still is.
    const s3 = await call("POST", "/api/submissions", {
      assignmentId: aid, studentName: " alex ", title: "Essay v2", text: ESSAY,
    });
    assert.ok(s3.body.report.sources.every((x) => x.sourceId !== s1.body.id));

    // With the library source gone, Alex's only overlap is their own draft,
    // which is excluded, so a re-run report drops to 0%.
    assert.equal((await call("DELETE", `/api/sources/${src.body.id}`)).status, 204);
    const re = await call("POST", `/api/submissions/${s3.body.id}/reanalyze`);
    assert.equal(re.body.report.similarity, 0);

    // A different student copying Alex is caught via the student paper.
    const s4 = await call("POST", "/api/submissions", {
      assignmentId: aid, studentName: "Jordan", title: "Copied", text: ESSAY,
    });
    assert.equal(s4.body.report.similarity, 100);
    assert.equal(s4.body.report.sources[0].kind, "submission");

    const list = await call("GET", `/api/submissions?assignmentId=${aid}`);
    assert.equal(list.body.length, 4);
    assert.ok(list.body.every((x) => x.text === undefined));

    const assignments = await call("GET", "/api/assignments");
    assert.equal(assignments.body[0].submissionCount, 4);

    // Data survives a restart.
    const reopened = createApp({ dataFile });
    assert.equal(reopened.store.list("submissions").length, 4);

    // Deleting the assignment removes its submissions.
    assert.equal((await call("DELETE", `/api/assignments/${aid}`)).status, 204);
    assert.equal((await call("GET", "/api/submissions")).body.length, 0);
  });
});

test("validation errors return 4xx with a message", async () => {
  await withServer(async (call) => {
    assert.equal((await call("POST", "/api/assignments", {})).status, 400);
    assert.equal((await call("POST", "/api/assignments", { title: "x", minMatchWords: 2 })).status, 400);
    assert.equal((await call("POST", "/api/assignments", { title: "x", dueDate: "nope" })).status, 400);
    assert.equal((await call("POST", "/api/assignments", "{bad json")).status, 400);
    const a = await call("POST", "/api/assignments", { title: "x" });
    const bad = await call("POST", "/api/submissions", { assignmentId: a.body.id, studentName: "A", title: "T", text: "  !!! " });
    assert.equal(bad.status, 400);
    assert.match(bad.body.error, /at least one word/);
    assert.equal((await call("POST", "/api/submissions", { assignmentId: "missing", studentName: "A", title: "T", text: "hi" })).status, 400);
    assert.equal((await call("GET", "/api/submissions/missing")).status, 404);
    assert.equal((await call("PATCH", "/api/sources")).status, 405);
    assert.equal((await call("GET", "/api/nope")).status, 404);
    const ext = await call("POST", "/api/extract", { filename: "a.exe", base64: "AAAA" });
    assert.equal(ext.status, 400);
  });
});

test("late submissions are flagged", async () => {
  await withServer(async (call) => {
    const a = await call("POST", "/api/assignments", { title: "Past due", dueDate: "2000-01-01T00:00:00Z" });
    const s = await call("POST", "/api/submissions", { assignmentId: a.body.id, studentName: "A", title: "T", text: "some words here" });
    assert.equal(s.body.late, true);
  });
});
