# Security and privacy

Academic documents can contain unpublished research and personal data. This
page lists what the system protects against, how, and what it does not cover.

## Data handling

| Data | Where | Protection |
|---|---|---|
| Uploaded original files | `backend/data/files/` (random 160-bit names) | Fernet-encrypted (AES-128-CBC + HMAC-SHA256), file mode 0600 |
| Extracted document text, reports, revisions, library sources | Database | Fernet-encrypted columns |
| Passwords | Database | scrypt (N=2^14, r=8, p=1, 16-byte salt) |
| Session tokens | Browser cookie; DB stores only SHA-256 of the token | HttpOnly, SameSite=Lax, Secure in production, 14-day expiry |
| Search-provider responses, fetched public pages | Database caches | Public data, stored in the clear, expires (7 / 30 days) |

**What leaves the server**

- To scholarly search services (OpenAlex, Crossref, Semantic Scholar, arXiv,
  Wikipedia) and, if configured, a web search API: short excerpts only:
  ~10-word phrases from the most distinctive sentences, keyword lists, and
  reference titles. Every query sent is listed in the report. Users can switch
  external search off (Settings → Privacy); then nothing leaves the server.
- To Anthropic's API: the selected passage, and only when a user explicitly
  requests an AI rewrite with the assistant enabled. Anthropic does not train
  on API data by default.
- Nothing else. Documents are never shared, published or used for training.

**Retention and deletion**: each user chooses a retention period (default 90
days); a background task deletes older documents, reports and files. Users can
delete any document, collection or their whole account at any time; deletion
removes database rows (cascading) and the encrypted file.

## Controls

| Threat | Control |
|---|---|
| Malicious or malformed uploads (zip bombs, parser exploits, huge PDFs) | Size limit (20 MB) enforced while streaming; type detected from magic bytes, not extension; parsing in a separate process with `RLIMIT_AS` 1 GB, `RLIMIT_CPU` 60 s, `RLIMIT_FSIZE` 0, wall-clock timeout, isolated interpreter (`-I -B`); DOCX zip inspected before parsing (member count, total size, compression ratio); PDF page cap; python-docx parser does not resolve external entities |
| Server-side request forgery via fetched source URLs | Only http/https on ports 80/443; every hop of a redirect chain is re-resolved and rejected if it points to private, loopback, link-local, reserved or multicast addresses; size caps; robots.txt honoured |
| XML attacks in provider feeds | arXiv Atom parsed with `defusedxml` |
| Cross-site request forgery | Per-session CSRF token required in a custom header on every state-changing request, plus SameSite=Lax cookies |
| Cross-site scripting | React escapes all text; no `dangerouslySetInnerHTML`; strict Content-Security-Policy (`default-src 'self'`, no inline scripts); report PDFs escape all document text before ReportLab markup |
| Clickjacking | `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'` |
| Account enumeration and brute force | Identical error for unknown email and wrong password; dummy hash comparison for unknown accounts; rate limits per IP and per email |
| Broken access control | Every query filtered by the signed-in user's id; other users' resources return 404; covered by automated tests |
| Secret leakage | API keys only in server environment variables; never sent to the browser; production refuses to start without `ENCRYPTION_KEY` and secure cookies |
| Unbounded cost | Per-document query budget and source cap; per-user upload and rewrite rate limits; word, page and size limits |
| Accountability | Audit log of sign-ins, failed sign-ins, uploads, exports, deletions, password and settings changes; users can view their own activity |

## Known gaps (be explicit)

- **Rate limits are per process.** Behind several API replicas, move them to
  a shared store (e.g. Redis) or the reverse proxy.
- **DNS rebinding.** The SSRF check resolves a hostname and then lets the HTTP
  client resolve it again; a hostile DNS server could answer differently the
  second time. Pinning the resolved IP, or an egress proxy with an allow-list,
  closes this.
- **Encryption key management.** The Fernet key sits in an environment
  variable. Production deployments should load it from a secrets manager and
  plan key rotation (Fernet supports `MultiFernet` for rotation; not wired up).
- **No multi-factor authentication or SSO.** Universities would normally put
  the app behind their identity provider (SAML/OIDC).
- **Search queries reveal excerpts to third parties.** They are short and
  disclosed, but for highly confidential work users should disable external
  search.
- **TLS** is expected to be terminated by a reverse proxy; the app sets HSTS
  when `COOKIE_SECURE=true`.
