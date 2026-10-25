# Graduation-project defence: questions and answers

Prepared answers to the questions a demanding examination committee is likely
to ask. Numbers refer to [EVALUATION.md](EVALUATION.md); weaknesses are stated
plainly because hiding them would not survive questioning.

---

## Scientific validity

**Q1. How do you distinguish plagiarism from similarity?**
We don't claim to detect plagiarism; we detect *overlap* and classify it.
Each matched passage is put into one of seven categories using two pieces of
evidence the system can actually observe: quotation marks around the passage,
and a citation in the same or next sentence. Correctly quoted and cited text
and reference-list overlap are shown but *not counted*. Copied wording with a
citation but no quotation marks is counted and labelled as a citation problem
(patchwriting), not as theft. Only text with neither quotation nor citation is
labelled "possible unattributed copying". The final judgement, which depends
on intent, discipline norms and assignment rules, is left to a human; every
report says so.

**Q2. How are false positives handled?**
Four mechanisms, each measured:
1. A minimum match length of 8 words (and at least 4 content words) removes
   stock phrases. On original text from the Brown corpus, 0.8% of passages were
   flagged in the default view.
2. Attribution rules (Q1) keep cited quotations and bibliographies out of the
   score.
3. Semantic matches are never added to the similarity index and are labelled
   by evidence strength; weak single-sentence matches (measured precision
   0.37–0.44) are hidden by default.
4. The AI-writing module uses a threshold chosen for ≤ 2% false positives on
   human validation text and an explicit "inconclusive" band.

**Q3. How does the system identify paraphrased passages?**
Every document sentence is compared with every candidate source sentence using
sentence embeddings. A pair must (a) exceed a calibrated cosine threshold,
(b) share at least 25% of content words unless the similarity is very high,
and (c) have comparable length. Consecutive document sentences that align
*in order* with consecutive sentences of the same source form one passage;
two or more aligned sentences count as strong evidence. Optionally a
cross-encoder rescores each pair jointly.

**Q4. How reliable is semantic similarity?**
Moderately, and we measured exactly how moderately. On MRPC, whose negatives
are topically related non-paraphrases, cosine similarity from the offline
word-vector model reaches AUC 0.71 and TF-IDF 0.70. In our document benchmark,
weak single-sentence matches were right less than half the time (precision
0.37–0.44), strong evidence reached 0.56–0.60. That is why semantic
results are a separate, clearly labelled number and never part of the
similarity index. A sentence-transformer model should improve this; we could
not calibrate it in the development sandbox and say so.

**Q5. What happens when two documents discuss the same topic?**
That is precisely MRPC's negative class and our "same-genre original text"
class. Exact matching is unaffected: topical documents rarely share 8
consecutive words, and when they do (15 of 60 MRPC negative pairs did) that
shared wording is genuinely there. For semantic matching, the topicality
filter (shared content words + length + in-order alignment) keeps the
default-view false-positive rate on topical negatives at 6–7%. It is not
zero, which is why every semantic finding shows both sentences side by side
and says that shared vocabulary alone is not plagiarism.

**Q6. Can the system detect uncited sources?**
Yes, if the source is reachable. A match without nearby quotation or citation
is the "possible unattributed copying" category. Two limits: the source must
be found (open web needs a search API key; paywalled bodies are invisible),
and "citation nearby" is a heuristic. We do not prove that the citation
refers to the matched source.

**Q7. What are the limitations of AI-text detection?**
Fundamental: generated text has no physical watermark; detectors measure style
statistics that overlap with human writing (formal, formulaic, non-native),
and editing moves text across the boundary. Specific to ours: one generator
family in training; news-style human text flagged at 9%; mixed documents
mostly read as human at document level. Our response: banded verdicts instead
of a percentage, an inconclusive band (10% of validation texts), paragraph
results, the per-feature explanation, and caveats on every report.

**Q8. Did you check bias against non-native writers?**
Yes, on the ELLIPSE corpus of English-language-learner essays (grades 8–12):
false-positive rate 0% (lower and middle proficiency) and 1.8% (higher
proficiency) at the "many indicators" threshold. This follows Liang et al.
(2023), who showed commercial detectors misclassify most TOEFL essays. Our
features were sign-constrained precisely so the model cannot learn "unusual
words = AI" from a corpus quirk.

---

## Engineering

**Q9. Why this architecture?**
One FastAPI process serves the API and the built React app; workers (threads
or separate containers) run analyses from a database-backed queue. Each
choice removes infrastructure that would not improve results: no Redis
(PostgreSQL already gives `FOR UPDATE SKIP LOCKED` claiming and durable
progress), no vector database (≤ 50 sources per document fit in memory), no
Next.js (everything is behind sign-in, so SSR adds nothing). Trade-offs:
polling instead of push, per-process rate limits. See ARCHITECTURE.md.

**Q10. Why these NLP models?**
- Exact matching: word 5-gram fingerprints, the standard in plagiarism
  detection (Broder 1997; Schleimer et al. 2003). Precise and explainable.
- Semantic: Sentence-Transformers MiniLM is the de facto efficient sentence
  encoder; offline we use SIF-weighted word vectors (Arora et al. 2017), a
  well-known strong baseline, so the system still works without model
  downloads; TF-IDF is the last-resort fallback and is flagged "degraded".
- AI indicators: logistic regression over stylometric features, chosen for
  transparency and calibration on small data rather than raw accuracy.
- Language ID: langdetect (character n-gram Naive Bayes, 55 languages).

**Q11. Why embeddings and not just string matching?**
String matching misses paraphrase entirely; on the paraphrase cases of our
benchmark, exact matching alone finds only the verbatim fragments. Embeddings
recover 57–67% of paraphrased paragraphs. But embeddings also fire on topical
similarity, which is why they are a second, separate signal.

**Q12. How is source discovery automated?**
From the document alone: (1) phrase queries, the most distinctive 10-word
windows (rare vocabulary, numbers, named entities) spread across the document;
(2) keyword queries for the document and its longest paragraphs; (3) the
titles of its own references. Queries go to every enabled provider under a
budget; candidates are merged (DOI/URL/title), ranked by evidence (exact-phrase
hits weigh most), and the best are fetched in full where open (Wikipedia API,
open-access PDFs, robots-permitted pages). Mirrors are collapsed with
MinHash-LSH. Every query sent is listed in the report.

**Q13. How does the system scale?**
Workers scale horizontally (job claiming is atomic). Per-document cost is
bounded by the query budget and source cap. Measured offline: 1,000–20,000
words in 1–6 s per document after discovery; 6 concurrent 3,000-word jobs on 2
worker threads in 7.6 s (~48 jobs/min); 368 MB peak memory. Discovery adds
network latency (provider rate limits, e.g. arXiv 1 request/3 s), mitigated by
parallel queries and caching.

**Q14. How are external service failures handled?**
Per-host rate limiting, retries with backoff on 429/5xx honouring
Retry-After, immediate failure on connection errors, and a circuit breaker
that stops calling a provider after repeated failures. A failed provider never
fails the analysis: the coverage statement marks it "unavailable", and if no
provider answered the report says, in a warning banner, "This is a coverage
gap, not a clean result". This exact path was exercised in the development
sandbox, where all external services were blocked.

**Q15. What happens when a source is behind a paywall?**
We never bypass access controls. If only metadata and abstract are available,
we compare against the abstract and label the source "abstract only". If an
open-access copy exists (OpenAlex/Semantic Scholar/arXiv), we use that.
Unreachable full texts are listed among the coverage limitations.

**Q16. How is the system evaluated?**
Four layers: (1) 89 automated tests including malicious files, security
controls, authorization isolation and the full pipeline against a fake
internet; (2) MRPC calibration of the paraphrase rule on train, evaluation on
test; (3) a 60-document benchmark with known ground truth for passage-level
precision/recall, span accuracy and score error; (4) AI-indicator
cross-validation on 554 texts across four genres plus robustness to editing
and mixing. Performance measurements cover extraction, stage latency, memory
and concurrency. A live retrieval experiment is provided for environments with
internet access.

---

## Academic contribution

**Q17. What makes this project different from existing solutions?**
Not raw detection power: Turnitin has a database we cannot have. The
difference is *transparency and honesty by construction*: every score comes
with its formula; every match with the source passage, the evidence and the
reason; every report with a statement of what was and was not searched;
semantic and AI signals are kept apart from the overlap score and banded by
strength. It is also free of private databases, self-hostable, and couples
detection with a writing studio that teaches rather than disguises.

**Q18. What were the main research and engineering challenges?**
- Telling paraphrase from topical similarity (still the weakest point).
- Building a fair AI-indicator evaluation: our first model scored AUC 1.0
  because it learned "docstring vs prose". We replaced the human data with
  same-genre corpora (Brown, ELLIPSE) and added sign constraints.
- Making ground truth honest: MRPC "paraphrases" often keep 8+ identical
  words. Our benchmark initially counted correct detections of that wording
  as errors, until we measured the shared runs.
- Safe document parsing and safe fetching of arbitrary URLs.

**Q19. What did you build yourselves vs. third-party?**
Built: the fingerprint matcher and gap-tolerant chaining, attribution and
classification, the semantic alignment and topicality filter, MinHash-LSH
de-duplication, query generation, provider orchestration and the coverage
model, citation analysis, the stylometric features and constrained logistic
regression, quality checks, the rule-based editor and change tracking,
sandboxing, the job system, the report and the UI. Third-party: text
extraction libraries, word frequencies, language ID, embedding models, search
APIs, the optional LLM.

**Q20. Which parts depend on third-party APIs?**
Source discovery (OpenAlex, Crossref, Semantic Scholar, arXiv, Wikipedia;
optionally Brave or Google) and the optional AI writing assistant (Anthropic).
Everything else runs on the server. Without the APIs, the system still checks
the user's library and collections, citations, AI indicators and quality, and
states the coverage gap.

**Q21. What are the measurable innovations?**
- A weak/strong evidence policy for semantic matches, derived from measured
  precision (0.37–0.44 for weak single-sentence matches vs 0.56–0.60 for
  strong evidence). Hiding weak signals by default cuts the share of topical
  non-paraphrase passages that get flagged from 23–36% to 6–7%, at the cost
  of missing more single-sentence paraphrases.
- A sign-constrained stylometric detector with a documented 2.1% false-positive
  rate overall and 0–1.8% on non-native writers.
- A coverage statement that turns "0% similarity" into "0% among N sources
  from these services", or into an explicit coverage-gap warning.

**Q22. What are the real limitations?**
See LIMITATIONS.md. The top three: no private repositories (and no open-web
search without a paid key); paraphrase detection of moderate accuracy; AI
indicators validated on one generator family and a few genres.

**Q23. What evidence demonstrates that the system works?**
The committed result files in `backend/eval/results/` (reproducible with the
commands in DEPLOYMENT.md), the 89-test suite, and a live demonstration:
upload a document containing a copied paragraph, a cited quotation and a
paraphrase, and walk through each highlight's evidence. For the "unfamiliar
document" demonstration, run with internet access and show the coverage
statement listing which services answered.

**Q24. If a professor challenges a specific finding, what can you show?**
For any highlight: the exact passage, the source passage, the source's URL or
DOI and how much of it we could read (full text or abstract), the matching
method, the score and its meaning (word coverage or cosine), whether a
citation was found, the category rule that fired, and the formula that turned
it into the percentage. For AI indicators: each feature's value, typical human
and AI values, and its contribution to the score.
