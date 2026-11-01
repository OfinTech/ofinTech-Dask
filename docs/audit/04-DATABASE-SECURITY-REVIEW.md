# 04 — Database and storage review

Scope: `engine/desk_engine/db.py` (schema, migrations, access layer), `storage.py` (file objects), the dataset
tables, backups, and whether SQLite remains the right choice for each deployment mode.

## 1. What is there

- **Engine:** SQLite, WAL journal, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=30 s`, one connection
  per thread, every write inside `BEGIN IMMEDIATE` behind an in-process `RLock` (`Database.tx`).
- **Schema:** 52 tables created by 18 ordered migrations, version in `PRAGMA user_version`, each migration one
  transaction; migrations are append-only and never rewritten after release (`CHANGELOG.md`). 47 foreign keys, 46
  of them `ON DELETE CASCADE` from `workspaces`/`users`, so deleting a workspace removes its rows atomically.
  24 indexes, all on the access paths the routes use (`(workspace_id, …)` prefixes, thread message order, pending
  webhook deliveries, chunk order). Three FTS5 virtual tables (document chunks, messages, memories).
- **Datasets:** one table `ds_<id>` per import/sync with generated column keys `c0..cN` and a `_row` ordinal;
  column names from files never become identifiers (`data/store.py`); queries are compiled from a declarative
  spec with bound parameters and a 10,000-row cap (`data/query.py`).
- **Access layer:** `one/all/scalar/execute/insert/update` with identifier validation; string-built SQL only
  for identifiers and `?` placeholder lists (36 sites reviewed, document 02).
- **Secrets:** `secrets` table holds AES-256-GCM ciphertext with associated data `id|purpose|workspace`.
- **Files:** `LocalStorage` (0600, atomic rename) or `S3Storage`, always under `EncryptedStorage` (HKDF key,
  nonce per object, key as AAD). After OB-05 an unauthenticated plaintext object is refused.
- **Backups:** `python -m desk_engine backup <file>` uses the SQLite online backup API; `server/backup.sh`
  copies it out of the container with a checksum. The copy is **plaintext** except for the `secrets` column.
- **Logs:** uvicorn access log is off; application logs carry ids and exception types; after OB-13 no token-bearing
  URL (Telegram) reaches a log; SQL statements are never logged.

## 2. Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| DB-01 | P2 | Backups are plaintext: ledger data, documents' extracted text, conversations and memories are readable by anyone with the file. The master key is kept apart, which protects credentials only. | open — owner decision 4 (encrypt `backup.sh` output with an operator-held key; or `sqlite3` into an encrypted volume) |
| DB-02 | P2 | Synchronous SQLite calls run on the asyncio event loop; a large query (400k-line ledger aggregation) or a slow checkpoint stalls every request and every SSE stream for its duration. | open — mitigation below (R9) |
| DB-03 | P3 | No periodic `wal_checkpoint`; with long-lived connections the WAL can grow between automatic checkpoints under write load. | open — add `PRAGMA wal_checkpoint(TRUNCATE)` to the scheduler every few hours (R9) |
| DB-04 | P3 | Dataset tables live in the application database; a 400k-line import inflates the same file that holds sessions and secrets and is copied by every backup. | accepted for 1.x; see §4 |
| DB-05 | P3 | `secrets.workspace_id` is nullable (app-level OAuth credentials) and has no FK; `SecretStore.get` compares it, so isolation holds, but the schema does not say so. | accepted; documented here |
| DB-06 | P3 | No data-retention policy or secure deletion. FTS indexes are kept in step by triggers (`doc_chunks_ad`, `messages_fts_ad`, `memories_ad`), so deleted rows leave the index at once; freed pages, however, keep their bytes in the file until overwritten or vacuumed. | open (R9: `PRAGMA secure_delete` on the desktop database, a retention setting per workspace) |
| DB-07 | info | `users.email` is `COLLATE NOCASE UNIQUE`; sign-in lower-cases; Google sign-in links by `google_sub` first. Sound. | — |
| DB-08 | info | Temporary files: dataset upload spool is stored in encrypted storage under `uploads/{ws}/{id}` with a 1-hour expiry evaluated on the next upload; OCR uses `TemporaryDirectory` on a tmpfs after R6. Sound. | — |

## 3. Tenancy at the database level

Every workspace-owned table carries `workspace_id` and is queried with it; `load_workspace` joins `members`.
Datasets add a second level: the table name `ds_<id>` is looked up through `datasets` by `(id, workspace_id)`
before any SQL touches it (`store.get_dataset`). Secrets are bound by AAD, so even a copied ciphertext does not
decrypt in another workspace. Tests: `test_team_server` (API), `test_knowledge` (PipesHub), `test_ai_security`
(through the agent's tools). No gap was found.

## 4. Is SQLite the right choice?

**Desktop: yes, without reservation.** One user, one process, data under the user's profile, no administration,
online backup API, encryption of the sensitive columns and of every file object. Nothing a client/server database
would add is wanted on a laptop.

**Team Server: yes for the sizes the product targets today, with three conditions.** A team-sized server (tens
of users, a few workspaces, ledgers in the hundreds of thousands of lines) is well within SQLite's throughput when
writes are serialised, which they are. The conditions:

1. **Keep heavy reads off the loop** (DB-02). The cheapest, non-invasive fix is to run the known-heavy calls
   (dataset import commit, ledger analysis, close preparation, cost-centre analysis, FTS re-index) in
   `asyncio.to_thread`, which the code already does in several places (40 call sites); extend it to the dataset
   query path and the import commit. Because each thread has its own connection and writes take the lock, this is
   safe today.
2. **Checkpoint on a schedule** (DB-03).
3. **Encrypt backups** (DB-01).

**When PostgreSQL becomes necessary:** more than one engine process (horizontal scaling, zero-downtime deploys),
ledgers beyond a few million lines with concurrent analytical queries, or a customer requirement for row-level
security and managed-database controls. The code is already shaped for it: a single access layer, parameterised
SQL, no SQLite-specific SQL outside FTS5 and `PRAGMA`. A migration would need: (a) an FTS replacement
(PostgreSQL `tsvector` or keeping FTS5 in a sidecar), (b) `ds_*` tables as ordinary tables or a JSONB/columnar
layout, (c) the in-process event hub and rate limiters replaced by Redis or PostgreSQL `LISTEN/NOTIFY`, (d) a
one-shot migration tool that reads the SQLite file and writes PostgreSQL in dependency order, re-encrypting
nothing (ciphertexts and AAD are unchanged). Operational cost: a managed PostgreSQL and its backups, versus a
volume. **Recommendation:** do not migrate in 1.x; add the three conditions above, and make "multiple engine
processes" the trigger for the PostgreSQL work in document 07.

## 5. Sensitive data in the database

| Data | Table(s) | Protection |
|---|---|---|
| Passwords | `users.password_hash` | scrypt |
| Session, invite, reset, handoff, ingest, webhook, MCP tokens | various `*_hash` columns | SHA-256 of random ≥ 24-byte tokens, single use where applicable |
| API keys, OAuth tokens, bot tokens, MCP headers | `secrets.ciphertext` | AES-256-GCM, AAD-bound |
| Ledger data, invoices, bills | `ds_*` | plaintext in the DB file (file mode 0600, container volume); encrypted file objects for uploads |
| Conversations, tool results, memories | `messages`, `memories` (+ FTS) | plaintext; bank identifiers masked before they reach a model, not before storage |
| Extracted document text | `doc_chunks` (+ FTS) | plaintext; original bytes encrypted in storage |
| Audit log | `audit_log` | append-only by code (no UPDATE/DELETE path); credential-like keys stripped from details forwarded to webhooks |

Bank identifiers are masked for the model but stored in full in tool results (`messages.content` holds the
masked text; `payload.blocks` may hold the full table). That is by design (reconciliation needs the last four and
the UI shows the user their own data), and it is documented in `docs/DATA_BOUNDARIES.md`.

## 6. Recommendations (carried into the roadmap)

1. DB-01: encrypted backups (owner decision on key management).
2. DB-02: `to_thread` for dataset query and import commit; measure with the 400k-line fixture (R9 performance).
3. DB-03: scheduled WAL checkpoint.
4. DB-06: `secure_delete` on the desktop database; a retention setting per workspace.
5. Document the PostgreSQL trigger conditions in 07 and keep the access layer free of SQLite-only SQL.
