"use strict";

// JSON-file persistence. Writes go to a temp file that is renamed over the
// real one, so a crash mid-write never leaves a half-written database.

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const EMPTY = () => ({ assignments: [], submissions: [], sources: [] });

class Store {
  constructor(file) {
    this.file = file;
    this.data = EMPTY();
    if (file && fs.existsSync(file)) {
      const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
      this.data = { ...EMPTY(), ...parsed };
    }
  }

  save() {
    if (!this.file) return;
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.data));
    fs.renameSync(tmp, this.file);
  }

  static id() {
    return crypto.randomUUID();
  }

  list(kind) {
    return this.data[kind];
  }

  get(kind, id) {
    return this.data[kind].find((x) => x.id === id) || null;
  }

  insert(kind, record) {
    this.data[kind].push(record);
    this.save();
    return record;
  }

  update(kind, id, patch) {
    const record = this.get(kind, id);
    if (!record) return null;
    Object.assign(record, patch);
    this.save();
    return record;
  }

  remove(kind, id) {
    const before = this.data[kind].length;
    this.data[kind] = this.data[kind].filter((x) => x.id !== id);
    if (kind === "assignments") {
      this.data.submissions = this.data.submissions.filter((s) => s.assignmentId !== id);
    }
    const removed = this.data[kind].length !== before;
    if (removed) this.save();
    return removed;
  }
}

module.exports = { Store };
