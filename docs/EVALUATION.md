# Evaluation methodology and results

Everything here is reproducible: the commands are listed below, raw outputs
are committed in [`backend/eval/results/`](../backend/eval/results), and all
randomness is seeded (cross-validation folds and feature hashing were made
deterministic after we found run-to-run variation; see "Lessons" below).

| What | Command | Result file |
|---|---|---|
| Automated tests | `pytest` | 89 passed |
| Paraphrase calibration (MRPC) | `python -m eval.similarity --calibrate` | `paraphrase_calibration.json` |
| Document-level similarity benchmark | `python -m eval.similarity --benchmark` | `similarity_benchmark.json` |
| AI-writing indicators | `python -m eval.ai_detection --train` | `ai_detection.json` |
| Performance | `python -m eval.performance` | `performance.json` |
| Live source retrieval | `python -m eval.retrieval_live --n 20` | not run (see below) |

Datasets (downloaded by the scripts, not redistributed):

- **MRPC**: Microsoft Research Paraphrase Corpus (Dolan & Brockett 2005):
  4,076 train / 1,725 test news sentence pairs, human-labelled paraphrase or not.
- **Brown corpus** (Francis & Kučera 1961, via NLTK data): 500 samples of
  published American English across genres.
- **ELLIPSE** (Crossley et al. 2023, CC BY-NC-SA 4.0): essays by English-
  language learners, grades 8–12, with proficiency scores.
- **CPython standard-library docstrings**: human technical prose, PSF licence.
- **LLM samples** (`eval/data/ai_samples.txt`): 78 texts generated for this
  project by a current commercial LLM (Anthropic Claude family): technical
  paragraphs, encyclopedic essays and student essays on the ELLIPSE prompts,
  in default voice and in a "write like a high-school student" persona.

---

## 1. Similarity

### 1.1 Paraphrase decision rule (MRPC)

Why MRPC: its negative pairs are *topically related sentences that are not
paraphrases*: the hardest realistic false-positive case. Thresholds were
selected on the train split (precision-weighted F0.5; "strong" = lowest
cosine with ≥ 95% train precision) and evaluated once on the test split with
the exact rule used by the engine (cosine threshold + content-word overlap +
length ratio).

| Provider | Paraphrase / strong threshold | Test AUC (cosine) | Precision | Recall | F1 | False-positive rate |
|---|---|---|---|---|---|---|
| Word vectors (SIF, spaCy md) | 0.91 / 0.98 | 0.713 | 0.773 | 0.716 | 0.743 | 0.417 |
| TF-IDF (lexical fallback) | 0.51 / 0.93 | 0.700 | 0.768 | 0.697 | 0.731 | 0.417 |
| Baseline: always "paraphrase" | – | – | 0.665 | 1.000 | 0.799 | 1.000 |
| Baseline: ≥ 50% shared words | – | – | 0.686 | 0.973 | 0.804 | 0.886 |

Reading: F1 favours the trivial baseline because 66% of MRPC test pairs are
positive; the metric that matters for an integrity tool is the false-positive
rate, which the calibrated rule cuts from 0.89–1.00 to 0.42. Even so, **42% of
topical non-paraphrases still pass at sentence level**: static word vectors
cannot reliably separate "same event, different claim" from "same claim, new
words". This result directly shaped the product (semantic matches are a
separate signal, labelled by strength; §1.3). Sentence-Transformers was not
available in the sandbox; the script evaluates it automatically when
installed.

### 1.2 Document-level benchmark

60 synthetic documents with known ground truth, compared against 50 sources
(~91,000 words): 40 Brown texts plus "wire" documents holding the second
sentence of MRPC test pairs. Each document mixes:

| Passage type | Count | Ground truth |
|---|---|---|
| Original text (held-out Brown files, never in any source) | 120 | not a match |
| Verbatim copy of a source span (25–70 words) | 24 | exact match |
| Near-copy: span with 8% of words replaced | 24 | near-exact match |
| Quoted + cited copy | 12 | quoted_cited, not counted |
| Single MRPC paraphrase sentence | 24 | paraphrase |
| Three consecutive MRPC paraphrase sentences | 30 | paraphrase (multi-sentence) |
| MRPC non-paraphrase sentence (topical negative) | 60 | not a paraphrase |
| Three consecutive MRPC non-paraphrases | 30 | not a paraphrase |

Results (default view = weak single-sentence semantic signals hidden, as in the UI):

| Metric | Word vectors | TF-IDF |
|---|---|---|
| Recall: verbatim copies | **1.00** | **1.00** |
| Recall: near-copies (8% words changed) | **1.00** | **1.00** |
| Quoted+cited copies classified correctly (not counted) | 1.00 | 1.00 |
| Recall: multi-sentence paraphrase (any semantic flag) | 0.67 | 0.57 |
| Recall: single-sentence paraphrase (any semantic flag) | 0.33 | 0.04 |
| Span accuracy of exact matches (IoU with truth) | 0.905 | 0.905 |
| **Default view**: passage precision | **0.883** | **0.895** |
| **Default view**: false-positive rate on original text | **0.008** | **0.008** |
| **Default view**: false-positive rate on topical negatives | **0.067** | **0.056** |
| All flags shown: false-positive rate on original text | 0.083 | 0.008 |
| Semantic precision, strong evidence | 0.56 (5/9) | 0.60 (3/5) |
| Semantic precision, weak single-sentence signals | 0.37 (23/63) | 0.44 (15/34) |
| Time per document (91k source words) | 2.6 s | 3.8 s |

Similarity-index accuracy: mean absolute error 0.82 percentage points on the
6 documents where no MRPC pair shares 8+ identical words; 7.65 points over all
60. The difference is not engine error: 15 of 60 MRPC *non-paraphrase* pairs
and many *paraphrase* pairs share runs of 8+ identical words (often names and
quotations from the same news story). The engine correctly counts that wording;
the synthetic ground truth did not. Across the 60 topical-negative sentences,
18 exact-match flags fired, 15 of them on genuinely shared 8+-word runs; the
remaining 3 (plus one in a paragraph negative) were the only exact-match false
positives, and 4 flags in total were wrongly counted in the index.

### 1.3 What the results changed

- **Weak/strong evidence policy.** Single sentences with moderate similarity
  were right less than half the time, so they are hidden by default and
  excluded from the paraphrase percentage; they remain one click away
  ("Weak semantic signals").
- **Semantic matches never enter the similarity index.**
- **Calibrated thresholds** replaced our initial guesses (which had a 68%
  false-positive rate on MRPC for word vectors).

### 1.4 Not measured here: live retrieval

The development sandbox blocked outbound access to every search service, so
we could not measure whether discovery finds real sources. Provider code was
tested against recorded responses in each API's documented format (OpenAlex,
MediaWiki, Crossref, Semantic Scholar, arXiv), including outages, rate limits,
paywalls, robots.txt and redirects to private addresses. `eval/retrieval_live.py`
measures retrieval success on random Wikipedia articles; run it before
relying on the system and add its JSON to `eval/results/`.

---

## 2. AI-writing indicators

### 2.1 Method

12 stylometric features (sentence-length variation, mean sentence length,
MTLD, word commonness, LLM-associated vocabulary, formulaic openers,
paragraph uniformity, repeated trigrams, contractions, out-of-vocabulary
rate, punctuation variety, em dashes) → standardised → logistic regression
with L2 and balanced class weights. **Sign constraints**: a feature whose
learned direction contradicts the published expectation is dropped and the
model refit (dropped: paragraph uniformity, out-of-vocabulary rate). Two
thresholds define three bands; the "many indicators" threshold is the 98th
percentile of out-of-fold scores on human text (target ≤ 2% false positives).

Data: 476 human texts (212 Brown academic/essay/editorial, 44 Brown news,
160 ELLIPSE learner essays, 60 docstrings) and 78 LLM texts. Stratified
5-fold cross-validation by genre and label; all metrics below are out-of-fold.

### 2.2 Results

Thresholds: few ≤ 0.303 < inconclusive < 0.782 ≤ many.

| Subset | n | Precision | Recall | F1 | FPR (human → "many") | Inconclusive | AUC | ECE |
|---|---|---|---|---|---|---|---|---|
| All | 554 | 0.868 | 0.846 | 0.857 | **0.021** | 0.099 | 0.977 | 0.071 |
| Academic (Brown + LLM essays) | 236 | 0.808 | 0.875 | 0.840 | 0.024 | 0.140 | 0.985 | 0.111 |
| Student essays (ELLIPSE + LLM) | 190 | 0.963 | 0.867 | 0.912 | **0.006** | 0.058 | 0.994 | 0.036 |
| Technical (docstrings + LLM) | 84 | 1.000 | 0.792 | 0.884 | 0.000 | 0.012 | 0.994 | 0.054 |
| News (human only) | 44 | – | – | – | **0.091** | – | – | – |

Non-native writers (ELLIPSE), false-positive rate at "many indicators":

| English proficiency (1–5) | n | FPR | Marked "few" |
|---|---|---|---|
| Lower (< 2.5) | 8 | 0.000 | 1.000 |
| Middle (2.5–3.5) | 95 | 0.000 | 0.968 |
| Higher (≥ 3.5) | 57 | 0.018 | 0.912 |

Robustness (final model):

| Condition | n | "Many" | Inconclusive | "Few" |
|---|---|---|---|---|
| AI student essays, default voice | 15 | 1.000 | – | 0.000 |
| AI student essays, "write like a student" persona | 15 | 0.733 | – | 0.000 |
| AI texts after light rule-based editing | 78 | 0.821 | 0.141 | 0.038 |
| Mixed: human paragraph + AI paragraph (document level) | 20 | 0.100 | 0.150 | 0.750 |

Largest coefficients (standardised): burstiness −2.63, mean sentence length
−2.62, repetition −2.08, punctuation variety −1.82, MTLD +1.56, commonness
+0.59, formulaic openers +0.44, LLM vocabulary +0.22.

### 2.3 Interpretation and limits

- On in-genre data the indicators separate classes well (AUC ≈ 0.98) with a
  2.1% false-positive rate overall, 0.6% on student essays.
- **Genre shift matters**: 9.1% of human news paragraphs reach "many".
- **Persona prompting** lowers detection to 73%; heavier editing or
  paraphrasing tools would lower it further.
- **Mixed documents** mostly read as human at document level: the
  paragraph-level view is the right tool for them.
- Single generator family; ~2× more human than AI texts. HC3 or any labelled
  set can be evaluated with `--external` / `--download-hc3`.
- These numbers support "indicators worth a conversation", never "proof".

---

## 3. Performance (offline, word vectors, 4 vCPU)

| Measurement | Result |
|---|---|
| PDF extraction, 20 pages / 9k words | 0.45 s |
| PDF extraction, 100 pages / 45k words | 3.48 s |
| DOCX extraction, 10k words | 0.18 s |
| Upload request (returns immediately, job queued) | 0.03–0.10 s |
| Full analysis, 1,000 words, 10 library sources | 2.1 s (first job includes model warm-up) |
| Full analysis, 5,000 words | 1.0 s |
| Full analysis, 20,000 words | 6.3 s (AI-indicator paragraph analysis 4.6 s) |
| 6 concurrent jobs × 3,000 words, 2 worker threads | 7.6 s wall, ~48 jobs/min, all completed |
| Peak memory (process RSS) | 368 MB |

External discovery adds network time that depends on providers and rate
limits (arXiv allows 1 request / 3 s). It is reported per analysis in each
report's timings.

---

## 4. Automated tests (89)

Unit and integration tests run without network access by injecting a fake
internet that answers in the real APIs' formats.

| Area | Examples |
|---|---|
| Extraction | DOCX with tables, PDF header removal and de-hyphenation, encodings, encrypted PDF, scanned PDF, corrupt files, extension spoofing, zip bomb, size limit |
| Citations | Four citation styles, multi-citation linking, year mismatch, uncited quotations, late-only bibliography heading |
| Similarity | Verbatim, case/punctuation changes, near-exact with substitutions, common phrases ignored, quoted+cited not counted, patchwriting, bibliography overlap, single-source attribution, paraphrase strong/weak/cited, MinHash |
| Discovery | Each provider's parsing, end-to-end discovery with OA PDF and Wikipedia full text, reference DOI verification, outage reporting, circuit breaker, external search off sends nothing, SSRF (loopback, metadata IP, redirect to private address, ports, schemes), robots.txt |
| AI / writing | Insufficient text, non-English, ordering on human vs AI text, protected spans in rewrites, LLM integrity check blocking dropped citations, refusal handling, quality checks and formulas |
| API | Registration/login/logout, password rules, account-enumeration resistance, rate limiting, CSRF, cookie flags, security headers, cross-user isolation, full pipeline, DOCX upload and exports, cancellation (queued and running), crash retry, stale-job recovery, retention purge, collections with peer comparison and same-author exclusion, library, assistant fallback, revisions, account deletion, encryption at rest |

Frontend: TypeScript strict type-check, plus a scripted browser session
(Playwright) covering sign-up with a pending upload, live progress, the
report viewer and every tab, PDF export, the studio (rewrite, apply, save
version, compare), dark mode and a 390 px mobile viewport (no horizontal
scrolling, no console errors). Docker: multi-stage image built and run;
compose stack (PostgreSQL + API + separate worker) processed an analysis.

---

## 5. Lessons from the evaluation (things that went wrong first)

1. **A perfect score was a bug.** The first AI-indicator model scored AUC 1.0
   because the only human data was Python docstrings: it learned "code-like
   prose vs essays". Replacing it with same-genre human corpora and adding
   sign constraints produced honest numbers.
2. **Ground truth can be wrong.** MRPC "non-paraphrases" often share long
   verbatim runs; the benchmark first counted correct detections of that
   wording as errors. We now measure shared runs and report both views.
3. **Benchmark leakage.** "Original" paragraphs and source filler were first
   drawn from the same files, inflating false positives; the pools are now
   disjoint.
4. **Non-determinism.** Python's randomised string hashing changed CV fold
   assignment and TF-IDF feature hashing between runs (thresholds moved by
   ±0.04). Both now use deterministic ordering/hashing.
5. **Initial thresholds were guesses.** Calibration on MRPC changed them and
   reduced false positives substantially.
