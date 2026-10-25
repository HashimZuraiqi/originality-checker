# Known limitations

This page is deliberately blunt. Each limitation says what the effect is and
what would address it.

## Coverage of sources

- **No access to private repositories.** Turnitin's database of past student
  papers, publisher full texts behind subscriptions and institutional
  repositories that require login are not searched. Nothing in the system
  claims otherwise; every report lists exactly which services answered.
- **Open web needs a paid search API.** Without `BRAVE_API_KEY` or a Google
  Programmable Search key, general web pages (blogs, essay mills, course
  sites) are not searched. Wikipedia and scholarly sources still are.
- **Abstracts are not full texts.** Many scholarly records expose only an
  abstract. Overlap with the body of a paywalled paper cannot be detected;
  such sources are labelled "abstract only" in the report.
- **Live retrieval effectiveness is unmeasured here.** The development
  sandbox blocked outbound access to every search service, so retrieval was
  tested only against recorded API responses. `python -m eval.retrieval_live`
  measures real retrieval success; run it before relying on the system.
- **Query budget.** At most 24 queries per document (configurable). Very long
  documents are sampled by their most distinctive sentences, so a copied
  passage in an unremarkable sentence can be missed if the source is not
  found by other queries.
- **Peer comparison is per collection.** Documents are compared with other
  submissions only within the same collection, not across all users.

## Similarity analysis

- **Paraphrase detection is the weakest component.** On MRPC, the best
  available offline model (word vectors) reaches AUC 0.71; single-sentence
  matches with moderate similarity had precision 0.37–0.44 in our benchmark. The system therefore reports semantic findings separately,
  never adds them to the similarity index, hides weak single-sentence signals
  by default and labels the rest by evidence strength. Sentence-Transformers
  (`SEMANTIC=sbert`) should do better, but its thresholds could not be
  calibrated in the sandbox; run `python -m eval.similarity --calibrate` with
  it installed and update `SbertProvider` thresholds.
- **Heavily rewritten or translated text** is not detected. Cross-language
  plagiarism needs multilingual embeddings (the provider interface allows
  adding them).
- **Short overlaps are ignored by design** (default minimum 8 words) to avoid
  flagging common phrases; a copied 6-word key phrase is not reported.
- **Citation linkage is heuristic.** A citation "nearby" (same or next
  sentence) marks a match as cited; the system does not prove that the
  citation refers to the matched source.
- **Formulas, tables, figures and images** are not analysed; only the text
  layer of PDFs is read and scanned PDFs (images of text) are rejected with an
  OCR message.

## AI-writing indicators

- **Not proof, and cannot be.** The output is a banded indicator with an
  inconclusive band; the report says so in several places.
- **Single generator family.** The AI training/validation samples come from
  one LLM family. Other models, older models and future models may write
  differently.
- **Genre shift.** 9% of human *news* paragraphs reached the "many indicators"
  band versus 0.6% of student essays. Writing styles absent from the
  validation data (legal, scientific methods sections, poetry) may behave
  differently.
- **Non-native writers.** False positives on English-language learners were
  0–1.8% depending on proficiency, but the learner essays are from US
  secondary schools; other populations are untested.
- **Edited and mixed text.** Light rule-based editing left 82% of AI texts in
  the "many" band, but a determined human editor or a paraphrasing tool can
  move text into "inconclusive" or "few". Documents that mix human and AI
  paragraphs mostly read as human at document level; the paragraph-level view
  exists for that reason.
- **English only**, and at least 150 words.

## Writing assistant

- **The Claude path was tested against a simulated API**, not the live
  service (no API key in the development sandbox). The integrity check
  (blocking edits that drop citations, quotes or numbers) is tested; output
  quality of real rewrites is not measured.
- **The rule-based editor is shallow**: it tightens wording and tone but
  cannot restructure arguments, improve coherence or expand explanations.

## Engineering

- **Per-process rate limits** and an in-process job claim lock: fine for one
  API instance plus worker containers; multiple API replicas need limits at
  the proxy.
- **Polling** for progress (1 s) rather than server push.
- **No SSO/MFA**; universities would put the app behind their identity
  provider.
- **No OCR**, no `.doc` (legacy Word) or `.odt` support.
- **The frontend has no automated component tests**; it is type-checked and
  exercised end-to-end with a scripted browser session.
