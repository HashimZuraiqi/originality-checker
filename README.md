# Originality Checker

A self-hosted assignment turn-in system with a similarity (plagiarism) checker,
in the spirit of Turnitin. Instructors create assignments, students turn in
papers, and every paper gets a similarity report that highlights passages
matching other documents.

No dependencies: it runs on plain Node.js (20+) and stores data in a JSON file.

## Run it

```sh
cd originality-checker
npm start            # http://127.0.0.1:3000
```

Environment variables:

| Variable    | Default         | Purpose                         |
| ----------- | --------------- | ------------------------------- |
| `PORT`      | `3000`          | HTTP port                       |
| `HOST`      | `127.0.0.1`     | Interface to bind               |
| `DATA_FILE` | `data/db.json`  | Where assignments/papers are stored |

## Features

- **Assignments** with optional instructions, due date (late papers are
  flagged), and report settings: minimum match length, exclude quoted text,
  exclude a trailing bibliography/references section.
- **Turn in** papers by pasting text or uploading `.docx`, `.txt` or `.md`.
- **Similarity report** per paper: overall similarity index, color-coded
  highlights in the paper, a ranked match overview by source, and a
  side-by-side view of each matched passage against the source text.
- **Compared against** every document in the source library and every other
  student's paper (a student's own resubmissions to the same assignment are
  not counted against them). "Re-run report" refreshes a report after new
  papers or sources arrive.
- Print-friendly reports, dark mode, works on phones.

## How matching works

Text is split into words (case, punctuation and Unicode forms are
normalized) and compared as overlapping 5-word sequences. Runs of matched
words shorter than the assignment's minimum match length (default 8) are
ignored so common phrases do not count. Each matched word is credited to a
single source (the one with the most overlap claims first), so the per-source
percentages add up to the overall similarity index.

A similarity index measures overlapping text, not misconduct: quoted and
cited material is expected, so always review matches in context.

## Limits

- It only compares against documents you add (the source library and
  submitted papers); it does not search the internet.
- There are no user accounts. Run it on a trusted network or put it behind
  an authenticating reverse proxy if students and instructors share it.

## Tests

```sh
npm test
```
