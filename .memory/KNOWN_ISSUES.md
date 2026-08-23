# KNOWN ISSUES — LegalShield Agent

Originally catalogued at commit `d8e310b` ("init commit"). **The status ledger below is
authoritative** — the detailed entries that follow it are preserved as-written from the
original audit, so they describe the code *before* repair. Read an entry for the diagnosis
and evidence, read the ledger for whether it still applies.

Severity legend: **P0** breaks the documented happy path · **P1** likely failure or security
exposure · **P2** correctness/robustness debt · **P3** cosmetic, dead code, docs.

Each entry records how it was verified. Items marked *(inferred)* were reasoned from the code
but not executed against a live stack — the LLM provider and Docker services were not exercised.

---

## Status ledger

As of commit `49b537a`. Verified against a live Docker stack with a real LLM provider
(end-to-end analysis completed in ~175s, three agents, 11 risk + 26 tax findings,
24 KB counter-draft) and 237 passing tests.

| # | Issue | Status | Landed in |
|---|---|---|---|
| 1 | `partials/result.html` does not compile | **fixed** | `e94aa87` |
| 2 | `CREATE TYPE IF NOT EXISTS` is invalid PostgreSQL | **fixed** | `2a0c0ac` |
| 3 | Migrations bypassed by `create_all` | **fixed** | `2a0c0ac` |
| 4 | Polling stops on the first poll | **fixed** | `e94aa87` |
| 5 | No auth, rate limiting, or CORS | **fixed** | `PHASE6` |
| 6 | Prompt injection via contract text | **mitigated + contained** | `2a0c0ac`, `PHASE7` |
| 7 | No LLM timeout/retry; strict JSON parsing | **fixed** | `2a0c0ac` |
| 8 | No contract truncation | **fixed** | `2a0c0ac` |
| 9 | Failed enqueue strands the contract | **fixed** | `89cbac6` |
| 10 | Doubled `/v1` in provider URL | **fixed** | `2a0c0ac` |
| 11 | Missing unique constraint behind the upsert | **fixed** | `2a0c0ac` |
| 12 | Naive/aware datetime mixing | **fixed** | `2a0c0ac`, `9ec0723` |
| 13 | `_started_at` leaks into stored JSON | **fixed** | `2a0c0ac` |
| 14 | Template assumes every finding key exists | **fixed** | `e94aa87` |
| 15 | Duplicated polling of the status partial | **fixed** | `e94aa87` |
| 16 | Upload trusts the filename extension | **fixed** | `89cbac6` |
| 17 | Exact-string matching; unbounded RAG context | **fixed** | `2a0c0ac` |
| 18 | Seed data never loaded | **fixed** | `15cc37b` |
| 19 | No tests | **fixed** | all commits |
| 20 | CDN assets with no fallback | **fixed** | `9ec0723` |
| 21 | `README.md` does not match the repository | **fixed** | `9ec0723` |
| 22 | `worker_async.py` is dead code | **fixed** (deleted) | `9ec0723` |
| 23 | `langchain` declared but unused | **fixed** (removed) | `9ec0723` |
| 24 | Unused / misleading declarations | **fixed** | `9ec0723` |
| 25 | No health endpoint | **fixed** | `89cbac6` |
| 26 | Image build discarded by the bind mount | **partly fixed** | `49b537a` |
| 27 | Styling split between CSS and inline attributes | **partly fixed** | `9ec0723` |

### Notes on the non-"fixed" rows

**#6 — mitigated, and now contained.** Contract text is fenced between
`===== UNTRUSTED CONTRACT TEXT =====` markers, the sentinel is stripped from the payload so
a crafted contract cannot close the fence early, and all three system prompts state that
fenced text is data. This raises the cost of an attack; it does not eliminate it.

The *persistence* half is now closed. `clause_patterns` used to be global, so one successful
injection became permanent and cross-tenant: the crafted "finding" was stored and then
injected as RAG context into every other user's later analysis. Patterns are now scoped by
`owner_id` with two reserved sentinels — `global` (curated seed data, read by all, written by
none at runtime) and `legacy` (learned before ownership existed, retained for auditing but
never read). A reader sees own + global; a writer only ever writes under its own id, and
never bumps a global row's counter, because that would be a runtime write to shared state.
Blast radius of a successful injection is now one owner's own future analyses.

**#26 — partly fixed.** The image is now multi-stage and ships without `gcc`/`libpq-dev`,
and `backend/.dockerignore` exists (`.gitignore` had listed it as ignored, so the entire
host tree including `.git` was uploaded to the daemon on every build). The `./backend:/app`
bind mount is still there because `--reload` depends on it; a production compose file
should drop it. The Dockerfile carries a comment saying so.

**#27 — partly fixed.** Inline `font-family` declarations are gone from every template, so
the CSS fallback stacks actually apply, and `tests/test_static_assets.py` fails if one
returns. Layout-related inline `style` attributes remain; they are cosmetic debt, not a bug.

---

## What the repair added

New modules, all with tests:

| File | Purpose |
|---|---|
| `app/services/findings.py` | Normalise LLM output (severity aliases incl. Bahasa, confidence coercion, text caps); fence untrusted text |
| `app/services/upload_validation.py` | Content-first upload checks: magic bytes, binary detection, streaming size limit |
| `app/services/reaper.py` | Fail contracts stuck in `uploaded`/`processing` past `stuck_contract_timeout_seconds` |
| `app/services/seed_loader.py` | Bootstrap `clause_patterns` from `dataset1.json`; regulation list for the tax agent |
| `app/seed.py` | Idempotent `python -m app.seed` CLI, run before uvicorn |
| `app/api/routes_health.py` | `/health` (liveness) and `/health/ready` (Postgres + Redis, 503 when degraded) |
| `alembic/versions/0002_*.py` | `clause_patterns.fingerprint` + both unique constraints, with dedupe and SQL backfill |
| `alembic/versions/0003_*.py` | `contracts.job_id`, `contracts.error`, `(status, updated_at)` index |
| `app/security.py` | HMAC cookie signing, anonymous owner ids, constant-time token compare |
| `app/middleware.py` | Rate limit → access gate → owner identity, in that request order |
| `app/services/rate_limit.py` | Fixed-window limiter on Redis, fail-open to in-memory |
| `app/api/deps.py` | `load_owned_contract` — the single place the ownership rule lives |
| `alembic/versions/0004_*.py` | `contracts.owner_id` + index, legacy rows backfilled to `legacy` |
| `scripts/e2e_access_check.py` | Live-stack access-control check (needs a running stack) |
| `alembic/versions/0005_*.py` | `clause_patterns.owner_id`; unique key moves to `(owner_id, fingerprint)` |

One defect was found during repair rather than in the original audit, and is worth
remembering because it is invisible until the *second* job runs:

> **Per-loop engine scoping.** `db/session.py` created one process-wide `AsyncEngine`. RQ is
> synchronous and runs every job in a fresh `asyncio.run`, so the second job received an
> asyncpg connection created on the first job's now-closed loop and failed with
> `got Future attached to a different loop`. Engines and sessionmakers are now keyed by
> `id(asyncio.get_running_loop())`, and `dispose_engine()` runs at the end of each job, each
> reaper sweep, and API shutdown. See `tests/test_queue_and_reaper.py::TestSessionScoping`.

---

## P0 — Blocks the demo

### 1. `partials/result.html` does not compile — results page returns 500
**File**: `backend/app/templates/partials/result.html` (lines ~83–98)

The template contains a duplicated, half-deleted block: after the tax-compliance card closes at
line 82, lines 84–96 repeat the tail of an older "processing / failed / else" branch, including a
stray `{% elif %}`, `{% else %}`, and three duplicate `{% set %}` statements, without an
enclosing `{% if %}`.

Verified:
```
jinja2.exceptions.TemplateSyntaxError: Encountered unknown tag 'elif'.
  File "app/templates/partials/result.html", line 87
```
The other four templates compile cleanly. Because `/partials/{id}/result` is polled every 3 s from
`result.html`, every contract page produces a stream of 500s and **no findings or counter-draft are
ever displayed**.

Fix: delete the orphaned lines (removing lines 83–98 makes the template compile — confirmed) so
the file goes straight from the closing `</div>` of the tax card to the `<!-- Counter Draft -->`
card, leaving exactly one `{% if status != "done" %} … {% else %} … {% endif %}` structure.
Do not delete the counter-draft card or its `{% endif %}` at line 150.

### 2. `alembic upgrade head` fails — `CREATE TYPE IF NOT EXISTS` is not valid PostgreSQL
**File**: `backend/alembic/versions/0001_initial_schema.py` (lines 18–19)

```python
op.execute("CREATE TYPE IF NOT EXISTS contract_status AS ENUM (...)")
op.execute("CREATE TYPE IF NOT EXISTS agent_type AS ENUM (...)")
```
PostgreSQL has no `IF NOT EXISTS` clause for `CREATE TYPE`; this raises a syntax error.
Even if it were valid, the subsequent `sa.Enum(..., name='contract_status')` inside
`op.create_table` will emit its own `CREATE TYPE` and fail with "type already exists" unless
declared with `postgresql.ENUM(..., create_type=False)`.

Fix: wrap each type creation in a `DO $$ … EXCEPTION WHEN duplicate_object … $$` block (or
`ENUM(...).create(bind, checkfirst=True)`), and pass `create_type=False` to the column-level enums.

This is currently masked: `docker-compose.yml` never runs Alembic, and
`app/main.py` lifespan calls `Base.metadata.create_all` instead — so nobody has hit the broken
migration yet.

---

## P1 — Likely failure or security exposure

### 3. Migrations are bypassed at runtime; schema can silently drift
**Files**: `backend/app/main.py` (lines 20–25), `docker-compose.yml`

`create_all` creates tables from ORM metadata on every API boot. Consequences:
- the two indexes defined only in `0001_initial_schema.py`
  (`ix_analysis_results_contract_id`, `ix_contracts_status`) are **never created**;
- `alembic_version` is never stamped, so a later `alembic upgrade head` will try to create
  existing tables;
- `create_all` never alters existing tables, so any future model change is silently ignored in
  environments that already have a schema;
- with more than one `api` replica, concurrent `create_all` calls race.

Fix: run `alembic upgrade head` as the `api` service's startup command (or an init container) and
delete the `create_all` block, after repairing issue #2.

### 4. Polling stops permanently on the first poll — findings never appear
**Files**: `backend/app/static/app.js` (line 70), `backend/app/templates/partials/result.html` (line 7)

`resultApp.init` cancels all polling when swapped text matches any of
`selesai`, `gagal`, `Counter-Draft`, `counter_draft`. But the **processing** state of
`partials/result.html` renders "Biasanya selesai 30–90 detik" — which contains `selesai`.
So on the very first swap (while the contract is still `uploaded`/`processing`) the guard fires,
`hx-trigger` is stripped from both `#status-area` and `#result-inner`, and the page freezes until
the user manually reloads. This bites as soon as issue #1 is fixed.

Fix: stop matching on rendered prose. Emit an explicit terminal marker instead, e.g. have the
partials render `<div id="poll-state" data-status="{{ status }}">` and check
`data-status in ("done","failed")`, or return the HTMX response header `HX-Trigger` from the server
when the contract reaches a terminal state.

### 5. No authentication, authorization, rate limiting, or CORS policy
**Files**: `backend/app/main.py`, all of `backend/app/api/`

Every endpoint is fully public. Anyone who can reach the port can:
- upload up to 20 MB per request and trigger three LLM calls (agent C alone is `max_tokens=6000`)
  — an unmetered path to the account's API spend;
- read **any** contract by UUID via `/api/contracts/{id}/result` — UUIDv4 is unguessable, but
  there is no ownership model at all, so a leaked URL is a full disclosure of the contract text
  and analysis;
- POST `/api/contracts/{id}/send` repeatedly to write unbounded `negotiation_sends` rows.

`docker-compose.yml` also publishes PostgreSQL 5432 and Redis 6379 to the host with the
credentials `legalshield:legalshield` and no Redis password.

Fix before any deployment: an auth dependency plus an owner FK on `contracts`, per-IP rate
limiting on upload, and removal of the DB/Redis host port mappings outside local dev.

### 6. Contract text is injected into prompts unsanitised (prompt injection)
**Files**: `backend/app/agents/agent_*.py` (`_load_prompt`), `backend/app/agents/prompts/*.txt`

`raw_text` is spliced into the prompt by literal string replacement with no delimiting, escaping,
or instruction hardening. A contract crafted with "ignore previous instructions" text (trivially
embeddable as invisible/whitespace text in a PDF) can steer agent A to report no risks, or steer
agent C to emit a counter-draft favourable to the client. Because agent A's high-confidence output
is written to `clause_patterns` and re-injected into every future run
(`skill_store.save_new_patterns` → `get_active_patterns`), a single poisoned upload can
**persistently contaminate the shared knowledge base for all users**.

Fix: wrap untrusted text in explicit delimiters and instruct the model to treat it as data only;
validate findings against a schema before persisting; require human review (or a much higher bar
than `confidence >= 0.75`) before a pattern enters the store.

### 7. LLM calls have no timeout and no retry; strict JSON parsing fails whole agents
**Files**: `backend/app/services/llm_client.py`, `backend/app/agents/*` (`_parse_json_response`)

- `AsyncOpenAI` is constructed without `timeout=` or `max_retries=`, so a hanging provider hangs
  the job until RQ's `job_timeout=600` kills the worker process. The orchestrator's
  `except` block never runs in that case, so the contract is **stranded in `processing` forever**
  and the UI spins indefinitely. *(inferred)*
- `_parse_json_response` strips a single ``` fence then calls `json.loads` with no repair,
  no `response_format={"type":"json_object"}`, and no retry. Any preamble ("Here is the JSON:"),
  trailing commentary, or truncation at `max_tokens` fails the entire agent.
- Truncation is likely for agent C: `max_tokens=6000` must hold an entire rewritten contract as a
  single JSON string.

Fix: pass `timeout` and `max_retries` to the client; request JSON mode where the provider supports
it; add a bounded retry with a "return only JSON" reminder; add a terminal-state reconciliation
sweep for contracts stuck in `processing`.

### 8. Long contracts will exceed the model context — no chunking or truncation
**Files**: `backend/app/agents/*`, `backend/app/api/routes_upload.py`

A 20 MB PDF's full text is stored in `contracts.raw_text` and passed whole to all three agents;
agent C additionally receives both findings sets *and* the original text. There is no token
counting, chunking, map-reduce, or truncation anywhere. The upload guard is on **file bytes**,
not extracted characters. *(inferred)*

Fix: count tokens after extraction and either reject, truncate with a warning, or chunk per-clause
and aggregate.

### 9. A failed enqueue leaves the contract permanently `uploaded`
**File**: `backend/app/api/routes_upload.py` (lines 58–62)

`db.commit()` happens before `enqueue_analysis()`. If Redis is unreachable, the exception
propagates as a 500 **after** the row is committed, and nothing ever retries. The contract sits at
`uploaded` and the UI shows "menunggu antrian analisis" forever. There is no dead-letter handling,
no `job_id` column, and no reconciliation job.

Fix: enqueue via an outbox/transactional pattern, or catch the enqueue error and mark the contract
`failed`; persist `job_id` on `contracts` for observability.

### 10. README's Ollama instructions produce a doubled `/v1` path
**Files**: `README.md` (line ~117), `backend/app/services/llm_client.py` (line 16)

`get_llm_client()` builds `base_url=f"{settings.hermes_base_url}/v1"`, but the README tells users to
set `HERMES_BASE_URL=http://host.docker.internal:11434/v1`, yielding
`http://host.docker.internal:11434/v1/v1` — every request 404s.

Fix: strip a trailing `/v1` in `get_llm_client()` (or `rstrip("/")` + conditional append) and
correct the README.

---

## P2 — Correctness and robustness debt

### 11. No unique constraint backing the `analysis_results` upsert
**Files**: `backend/app/db/models.py`, `backend/app/agents/orchestrator.py` (`_save_result`)

`_save_result` does SELECT-then-INSERT keyed on `(contract_id, agent_type)` and calls
`scalar_one_or_none()`. Nothing at the DB level enforces that key, so a re-run (retry, duplicate
enqueue, or manual re-analysis) can create a second row; from then on `scalar_one_or_none()` raises
`MultipleResultsFound` and the orchestrator marks the contract `failed`.

Fix: add `UniqueConstraint("contract_id", "agent_type")` plus a migration, and switch to a real
`ON CONFLICT DO UPDATE`.

### 12. Naive/aware datetime mixing
**Files**: `backend/app/agents/agent_*.py`, `backend/app/agents/orchestrator.py`, `backend/app/services/mailer.py`

Agents record `datetime.utcnow().isoformat()` (naive, and deprecated in Python 3.12+) while the
orchestrator uses `datetime.now(timezone.utc)` (aware). `_dt()` feeds the naive values straight
into `TIMESTAMPTZ` columns, so PostgreSQL interprets them in the server timezone —
`started_at`/`finished_at` can be off by hours relative to `created_at`/`updated_at`.

Fix: standardise on `datetime.now(timezone.utc)` everywhere.

### 13. Internal `_started_at` / `_finished_at` keys leak into stored JSON and API responses
**Files**: `backend/app/agents/*`, `backend/app/api/routes_analysis.py`

Agents mutate their parsed result with `_started_at`/`_finished_at` before it is written to
`result_json`, so these bookkeeping fields are persisted in JSONB, returned by
`/api/contracts/{id}/result`, and (for agent C) also handed to `counter_draft`'s consumers —
duplicating the dedicated timestamp columns.

Fix: return `(result, started_at, finished_at)` as a tuple, or `pop` the private keys before persisting.

### 14. `partials/result.html` assumes every finding key exists
**File**: `backend/app/templates/partials/result.html` (lines 33–41, 64–74)

The template dereferences `f.severity`, `f.severity[0]`, `f.clause_type`, `f.original_text`,
`f.confidence`, `f.issue_type` without guards. Jinja's default `Undefined` raises on subscript and
on arithmetic, so a single malformed LLM finding (missing `severity`, or `confidence` as a string)
500s the whole partial. The severity→CSS-stripe mapping also depends on the first letter
(`c`/`h`/`m`/`l`), so an unexpected value like `"sedang"` silently loses its colour stripe.

Fix: validate findings with a Pydantic model at the agent boundary and drop/normalise bad entries;
use `|default(...)` in the template as a second line of defence.

### 15. Duplicated polling of the status partial
**Files**: `backend/app/templates/result.html` (lines 20–26), `backend/app/templates/partials/status.html`

`#status-area` polls `every 2s` and swaps `innerHTML`; the returned non-terminal banners *also*
carry `hx-get … hx-trigger="every 2s"` with `hx-swap="outerHTML"`. Both timers run, roughly
doubling requests and producing interleaved swaps.

Fix: keep the trigger in exactly one place — either the container or the partial.

### 16. Upload validation trusts the filename extension only
**File**: `backend/app/api/routes_upload.py` (lines 15–33)

`ALLOWED_MIMETYPES` is declared and never used; only the extension is checked, and
`file.content_type` is ignored. A non-PDF renamed to `.pdf` reaches pypdf (which raises a handled
422), but a `.txt` rename lets arbitrary binary content through the latin-1 fallback — it always
"decodes", so `raw_text` becomes mojibake that is then billed to the LLM. Also, the entire body is
read into memory *before* the size check, so 20 MB+ payloads are fully buffered before rejection.

Fix: check `content_type` against `ALLOWED_MIMETYPES`, sniff the `%PDF-` magic bytes, enforce the
size limit from `Content-Length` and while streaming, and require the decoded text to look like text.

### 17. Skill-store matching is exact-string; RAG context grows unbounded
**File**: `backend/app/services/skill_store.py`

- `pattern_name = f"{clause_type}:{severity}"` means the store holds at most one row per
  (clause_type, severity) pair — the *first* example seen wins, and every later variant merely
  increments `times_matched`. The `description`/`example_text` are never improved.
- Conversely, the same substantive clause reported at a different severity creates a duplicate row.
- `SIMILARITY_CHECK_FIELD` is dead: matching is `==`, not semantic, despite the module docstring's
  "similar name" claim.
- `get_active_patterns()` returns **all** active patterns with no `LIMIT`, and agent A injects all
  of them into the prompt. As the store fills, prompt size (and cost) grows monotonically until the
  context window is exceeded. *(inferred)*
- `is_active` is never set to `False` anywhere; there is no way to retire a bad pattern except
  direct SQL.

Fix: key on a content hash or embedding similarity, cap the injected set (top-N by
`times_matched`), and add an admin path to deactivate patterns.

### 18. None of the shipped seed data is loaded
**Files**: `seed/dataset1.json` (100 records), `seed/datasetpasal1.json` (50 records)

Neither file is referenced anywhere in `backend/` — the only mention of `seed/` in the repo is
`README.md` pointing at `sample_contract.txt`. `dataset1.json` maps almost field-for-field onto
`clause_patterns` (`category`→`clause_type`, `contract_snippet`→`example_text`,
`risk_findings[].explanation`→`description`, `confidence`), so the "self-improving" store starts
completely cold on every fresh database even though 100 labelled examples are sitting in the repo.
`datasetpasal1.json` (KUHPerdata / UU citations with `pasal_relevan` and official URLs) would
directly serve agent B's `applicable_regulation` field.

Fix: add a `services/seed_loader.py` invoked once at startup (idempotent, guarded by a row count)
or as an explicit `python -m app.seed` command.

### 19. No tests exist, but the project is configured as if they do
**File**: `backend/pyproject.toml` (line 51)

`[tool.pytest.ini_options] testpaths = ["tests"]` — the `tests/` directory does not exist, so
`pytest` exits with "no tests ran". `asyncio_mode = "auto"` and `pytest-asyncio` are already
configured, so the harness is ready; nothing has been written.

Highest-value first tests: `_parse_json_response` against fenced/prefixed/truncated payloads;
`skill_store.save_new_patterns` threshold and increment behaviour; `routes_upload` validation
matrix; Jinja compilation of every template (would have caught issue #1); orchestrator behaviour
when agent A raises.

### 20. Front-end assets load from CDNs with no fallback
**File**: `backend/app/templates/base.html` (lines 7–10)

HTMX 1.9.12 (SRI-pinned) and Alpine 3.14.1 (**not** SRI-pinned) plus Google Fonts are fetched from
unpkg/googleapis at runtime. Offline or CDN-blocked environments get a page with no interactivity
whatsoever — the upload button, drag & drop, polling, and send all depend on them. Alpine without
integrity hash is also a supply-chain exposure.

Fix: vendor both libraries into `app/static/` (the project already has no build step, so this is a
straight file copy) or at minimum add SRI to the Alpine tag.

---

## P3 — Dead code, cosmetics, documentation drift

### 21. `README.md` does not match the repository
- Documents `backend/requirements.txt`; dependencies actually live in `backend/pyproject.toml`.
- The "Struktur Folder" tree omits `seed/dataset1.json`, `seed/datasetpasal1.json`,
  `backend/app/schemas.py`, `backend/app/worker_async.py`, and `backend/app/static/app.css`.
- The API table omits `/partials/{id}/status` and `/partials/{id}/result`.
- Every "Acceptance Criteria MVP" box is ticked, including "UI menampilkan klausul berisiko" —
  which cannot be true while issue #1 stands.
- The Ollama tip is wrong (issue #10).

### 22. `backend/app/worker_async.py` is dead code
`run_async_job` is defined and never imported anywhere; `services/queue.py._run_analysis_sync`
performs the same `asyncio.run` bridge. `app/worker.py`'s docstring also describes
"monkey-patching the import", which the code does not do.

### 23. `langchain` and `langchain-openai` are declared but never imported
**File**: `backend/pyproject.toml` (lines 17–18)

`grep` for `langchain` across `backend/app/**/*.py` returns nothing — agents use the `openai` SDK
directly. Two heavy dependency trees are installed into every image for nothing. The README's
Stack section also advertises "LangChain".

Fix: drop both pins, or actually adopt LangChain.

### 24. Unused / misleading declarations
- `backend/app/schemas.py` imports `uuid` and `EmailStr`, uses neither;
  `NegotiationSendRequest.recipient_email` is a plain `Optional[str]`, so invalid addresses are
  accepted and stored.
- `backend/app/config.py`'s `default_locale` is never read.
- `backend/app/api/routes_upload.py`'s `ALLOWED_MIMETYPES` is never read (see #16).
- `backend/app/db/session.py`'s `get_db()` is annotated `-> AsyncSession` but is an async generator
  (`-> AsyncIterator[AsyncSession]`).
- `backend/app/templates/upload.html` binds `:disabled="!fileName || loading"`, but `loading` is
  only ever set to `false` in `app.js` — the double-submit guard relies solely on the HTMX indicator.

### 25. No health endpoint
Neither `/health` nor `/healthz` exists (`grep` confirms). `docker-compose.yml` defines
healthchecks for `postgres` and `redis` but none for `api` or `worker`, so an api container that
booted but cannot reach the DB still reports healthy to any orchestrator.

### 26. Docker image build work is discarded by the bind mount
**Files**: `backend/Dockerfile`, `docker-compose.yml`

The image runs `pip install .` (installing the `app` package into site-packages) and `COPY . .`,
but both `api` and `worker` bind-mount `./backend:/app`, shadowing `/app` with the host tree. Only
the installed *dependencies* matter; the packaged copy of `app/` is never used. `gcc` and
`libpq-dev` are also left in the final image (no multi-stage build), and there is no
`.dockerignore` — though `.gitignore` curiously lists `.dockerignore` as an ignored path.

### 27. Frontend styling is split between `app.css` and large inline `style` attributes
`partials/result.html` and `upload.html` carry substantial inline styling alongside the
class-based system in `app.css`, so visual changes require editing both. Not a bug; noted because
it makes UI edits error-prone and inflates diffs.

---

## Remaining work

The original repair order is complete, as are the two security phases that followed it.
What is left, in the order it should be tackled:

1. **Integration tests against a live database.** The suite is 309 unit tests plus static
   guards; the migration chain, the orchestrator's upsert path, and the owner-scoped reads
   were verified by hand against Docker and by `scripts/e2e_access_check.py`, not by CI.
   `docker compose run --rm test` already starts Postgres and Redis, so the fixtures are the
   only missing piece.
2. **Production compose file** without the `./backend:/app` bind mount and without
   `--reload` (#26).
3. **Real SMTP** in `services/mailer.py` — still a logging stub, by design.
4. **Retire remaining inline layout styles** (#27).
5. **A real account system**, if the product needs contracts to survive a cleared cookie.
   The anonymous owner id is the right shape for it: replace `contracts.owner_id` with a FK
   to a `users` table and `middleware.OwnerMiddleware` with a session lookup. Nothing else
   needs to change, because `api/deps.py::load_owned_contract` is the only place the
   ownership rule lives.

### Operational notes worth keeping

- Tests run **in Docker only**: `docker compose run --rm --no-deps test`. The host Python is
  3.10 and lacks the dependencies; the project needs 3.11+.
- The `test` service sets `HERMES_BASE_URL=http://llm.invalid` inline, so no test can reach
  a real provider even if `.env` is populated.
- A full end-to-end analysis takes 150–200s against a hosted 70B model. `job_timeout` is
  600s and `stuck_contract_timeout_seconds` is 900s; keep that ordering if either changes.
- Re-running an analysis for the same contract is safe — the upsert updates the three
  existing `analysis_results` rows rather than adding more (verified).
- Docker Desktop's WSL engine crashed once mid-session during a rebuild. `docker desktop
  restart` recovered it with no data loss; the symptom is a `500 Internal Server Error` from
  the `/networks` API route.
- **Test a clean boot, not just a restart.** Two defects only appeared on the first boot of an
  empty volume: the `alembic_version` race between `api` and `worker` (fixed by the one-shot
  `migrate` service) and the per-loop engine failure on a worker's *second* job. Use
  `docker compose down && docker volume rm legalshield_postgres_data && docker compose up -d`.
