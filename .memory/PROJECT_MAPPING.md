# PROJECT MAPPING — LegalShield Agent

Structural map of the repository as it exists at commit `8233840`.
Line references are approximate anchors, not guarantees.

> Sections 2–13 were written at commit `d8e310b` and describe the architecture, which is
> unchanged. Where the repair altered behaviour, section 1 and the "Since `d8e310b`" section
> at the end are authoritative. `KNOWN_ISSUES.md` carries the full status ledger.

## 1. Repository tree (actual)

```
C:\legalshield\
├── .env.example                  # env template (git-tracked; .env is ignored)
├── .gitignore
├── docker-compose.yml            # postgres, redis, migrate, api, worker, test (profile)
├── docker-compose.prod.yml       # production overlay: no bind mount, no --reload, no DB ports
├── README.md                     # user-facing docs (Bahasa Indonesia)
├── .memory/                      # ← agent memory (this directory)
├── seed/                         # bind-mounted read-only into every service at /app/seed
│   ├── sample_contract.txt       # 3.9 KB deliberately abusive freelance contract (demo input)
│   ├── dataset1.json             # 100 labelled risky-clause records → bootstraps clause_patterns
│   └── datasetpasal1.json        # 50 Indonesian legal-reference records → tax agent citations
└── backend/
    ├── .dockerignore             # keeps .git/.venv/__pycache__ out of the build context
    ├── Dockerfile                # multi-stage; runtime image has no gcc/libpq-dev
    ├── pyproject.toml            # hatchling; deps + ruff + pytest config
    ├── alembic.ini
    ├── alembic/
    │   ├── env.py                # rewrites asyncpg URL → psycopg2 for migrations
    │   ├── script.py.mako
    │   └── versions/
    │       ├── 0001_initial_schema.py              # enums guarded by DO/duplicate_object
    │       ├── 0002_fingerprint_and_constraints.py # fingerprint + unique constraints
    │       ├── 0003_contract_job_tracking.py       # job_id, error, (status, updated_at)
    │       ├── 0004_contract_ownership.py          # owner_id (NOT NULL, indexed)
    │       └── 0005_per_tenant_clause_patterns.py  # clause_patterns.owner_id + per-owner uq
    ├── scripts/
    │   └── e2e_access_check.py    # live-stack access-control check (not in pytest)
    ├── tests/                    # 480 tests (417 unit + 63 integration)
    │   ├── conftest.py           # env defaults set before any app import; Jinja fixtures
    │   ├── test_templates.py     # compile + polling-marker regressions (#1, #4, #14, #15)
    │   ├── test_llm_client.py    # JSON extraction, base-URL normalisation (#7, #10)
    │   ├── test_findings.py      # severity/confidence coercion, fencing (#6, #8, #14)
    │   ├── test_skill_store.py   # fingerprint stability (#17)
    │   ├── test_migrations.py    # static guards on the migration chain (#2, #3, #11)
    │   ├── test_upload_validation.py  # magic bytes, binary detection, size (#16)
    │   ├── test_queue_and_reaper.py   # enqueue failure, per-loop engine (#9)
    │   ├── test_seed_loader.py   # dataset shape and prompt wiring (#18)
    │   ├── test_static_assets.py # vendored JS, font fallbacks, no inline styles (#20, #27)
    │   ├── test_mailer.py        # SMTP envelope, stub mode, failure contract, header injection
    │   ├── test_security.py      # cookie signing, access gate, limiter, ownership (#5)
    │   ├── test_deployment_config.py # compose guards: prod overlay, dev ergonomics (#26)
    │   └── integration/          # needs live Postgres; skipped under --no-deps
    │       ├── conftest.py       # creates/migrates/drops legalshield_test
    │       ├── test_db_schema.py # migration chain, model drift, fingerprint SQL parity
    │       └── test_app_behaviour.py # upsert, owner scoping, reaper, per-loop engine
    └── app/
        ├── __init__.py
        ├── main.py               # FastAPI app, lifespan, static mount, router wiring
        ├── config.py             # pydantic-settings Settings + cached get_settings()
        ├── security.py           # HMAC cookie signing, owner ids, token compare
        ├── middleware.py         # rate limit → access gate → owner identity
        ├── schemas.py            # Pydantic request/response models
        ├── worker.py             # RQ worker entrypoint + reaper thread
        ├── seed.py               # idempotent `python -m app.seed` CLI
        ├── db/
        │   ├── session.py        # per-event-loop engine registry, AsyncSessionLocal, Base, get_db()
        │   └── models.py         # Contract, AnalysisResult, ClausePattern, NegotiationSend
        ├── api/
        │   ├── deps.py           # load_owned_contract — the one ownership check
        │   ├── routes_pages.py   # HTML pages + HTMX partials
        │   ├── routes_upload.py  # POST /api/contracts/upload
        │   ├── routes_analysis.py# status + result JSON
        │   ├── routes_negotiate.py# POST /api/contracts/{id}/send
        │   └── routes_health.py  # GET /health, /health/ready
        ├── agents/
        │   ├── orchestrator.py   # A+B parallel → C sequential, upsert, status
        │   ├── agent_risk_clause.py      # also defines the shared AgentRun NamedTuple
        │   ├── agent_tax_compliance.py
        │   ├── agent_counter_draft.py
        │   └── prompts/{risk_clause,tax_compliance,counter_draft}.txt
        ├── services/
        │   ├── llm_client.py     # AsyncOpenAI factory, chat_completion_json, JSON extraction
        │   ├── findings.py       # normalise LLM output; fence untrusted contract text
        │   ├── upload_validation.py  # content-first upload checks
        │   ├── pdf_extractor.py  # pypdf text extraction
        │   ├── queue.py          # Redis/RQ enqueue (raises EnqueueError) + sync wrapper
        │   ├── rate_limit.py     # fixed-window limiter on Redis, fail-open to memory
        │   ├── reaper.py         # sweeps contracts stuck in uploaded/processing
        │   ├── seed_loader.py    # dataset1 → clause_patterns; datasetpasal1 → citations
        │   ├── skill_store.py    # per-owner clause pattern store (RAG context)
        │   └── mailer.py         # SMTP delivery; log-only stub when SMTP_HOST is empty
        ├── templates/
        │   ├── base.html         # nav/footer, vendored HTMX + Alpine, remote fonts
        │   ├── upload.html       # drag & drop upload, HTMX POST
        │   ├── result.html       # shell with two polling containers
        │   └── partials/{status.html,result.html}
        │                         # no template carries style= or :style=
        └── static/
            ├── app.css, app.js   # app.css owns all layout; ~60 classes added in phase 10
            └── vendor/{htmx-1.9.12.min.js,alpine-3.14.1.min.js}
```

## 2. Runtime topology

```mermaid
graph TD
    B[Browser HTMX plus Alpine] -->|POST /api/contracts/upload| API[FastAPI api service port 8000]
    B -->|GET status partial every 2s| API
    B -->|GET result partial every 3s| API
    API -->|INSERT contract| PG[(PostgreSQL 16)]
    API -->|enqueue job| R[(Redis 7 RQ queue legalshield)]
    W[worker service running app.worker] -->|dequeue| R
    W -->|asyncio.run run_analysis| ORC[orchestrator]
    ORC --> A[Agent A risk clause]
    ORC --> T[Agent B tax compliance]
    ORC --> C[Agent C counter draft]
    A --> LLM[OpenAI compatible LLM]
    T --> LLM
    C --> LLM
    ORC -->|upsert analysis results and update status| PG
    ORC -->|save new patterns| PG
    A -->|read active patterns for RAG| PG
```

## 3. End-to-end request flow

1. **Upload** — `routes_upload.upload_contract`
   - Validates filename extension against `ALLOWED_EXTENSIONS = {.pdf, .txt}` (extension only;
     `ALLOWED_MIMETYPES` is defined but never used).
   - Reads whole body into memory, rejects `> 20 MB` with 413.
   - `.pdf` → `pdf_extractor.extract_text_from_pdf` (pypdf); `.txt` → UTF-8 then latin-1 fallback.
   - Empty text → 422 ("Is it a scanned image?").
   - Inserts `Contract(status=uploaded)`, commits, then `enqueue_analysis(str(id))`.
   - Returns `ContractUploadResponse{id, filename, status}` as JSON.
2. **Client redirect** — `app.js › uploadApp.onUploadDone` parses the JSON body from the
   HTMX response and does `window.location.href = /contracts/{id}`.
3. **Queue** — `services/queue.py`
   - Lazily caches a module-level `Redis` connection and `Queue("legalshield")`.
   - Enqueues `_run_analysis_sync(contract_id)` with `job_timeout=600`.
   - `_run_analysis_sync` imports the orchestrator lazily and calls `asyncio.run(run_analysis(...))`.
4. **Worker** — `app/worker.py` builds `Worker([Queue("legalshield")])` and runs `work(burst=False)`.
5. **Orchestrator** — `agents/orchestrator.py › run_analysis`
   - Sets `status=processing`, loads `contract.raw_text`.
   - Phase 1: `asyncio.gather(risk, tax, return_exceptions=True)` — true parallelism.
   - Persists each result via `_save_result` (SELECT-then-update/insert upsert keyed on
     `contract_id + agent_type`); on exception stores `error` text and substitutes
     `{"findings": []}` so phase 2 can still run.
   - After A succeeds: `skill_store.save_new_patterns(db, findings)`.
   - Phase 2: `run_counter_draft_agent(raw_text, risk_result, tax_result)` — awaited, not parallel.
   - Sets `status=done`; any uncaught exception sets `status=failed`.
6. **Polling UI** — `result.html` mounts two divs with `hx-trigger="load, every 2s|3s"`
   hitting `/partials/{id}/status` and `/partials/{id}/result`.
   `app.js › resultApp.init` listens for `htmx:afterSwap` and strips `hx-trigger` once the
   swapped text contains `selesai`, `gagal`, `Counter-Draft`, or `counter_draft`.
7. **Send** — `routes_negotiate.send_negotiation` requires `status == "done"` (else 409),
   pulls `counter_draft` from the agent-C row, calls the `mailer` stub, inserts
   `NegotiationSend(status="stub")`.

## 4. Agent layer

All three agents follow the same shape:

| | Agent A | Agent B | Agent C |
|---|---|---|---|
| Module | `agent_risk_clause.py` | `agent_tax_compliance.py` | `agent_counter_draft.py` |
| Prompt | `prompts/risk_clause.txt` | `prompts/tax_compliance.txt` | `prompts/counter_draft.txt` |
| System role | "legal contract analyst" | "tax and compliance expert" | "legal contract drafter" |
| Temperature | 0.1 | 0.1 | 0.3 |
| `max_tokens` | 4096 | 3000 | 6000 |
| Extra input | RAG patterns from `clause_patterns` | — | A + B findings as JSON |
| Output keys | `findings[]` | `findings[]` | `counter_draft`, `summary_of_changes[]`, `negotiation_notes` |

Shared mechanics:
- `_load_prompt` reads the `.txt` template and does literal `{{ token }}` replacement.
- `_parse_json_response` strips a ``` fence if present, then `json.loads`. **No repair pass** —
  malformed JSON raises and the agent is recorded as failed.
- Each agent injects `_started_at` / `_finished_at` ISO strings into its returned dict; these
  are persisted inside `result_json` as well as mapped onto the row's timestamp columns.

Per-finding schema expected by the UI:
- Risk: `clause_type`, `severity` (`low|medium|high|critical`), `original_text`, `explanation`,
  `recommendation`, `confidence` (float).
- Tax: `issue_type`, `severity`, `original_text`, `explanation`, `recommendation`,
  `applicable_regulation`.

## 5. Data model (`app/db/models.py`)

```mermaid
erDiagram
    CONTRACTS ||--o{ ANALYSIS_RESULTS : has
    CONTRACTS ||--o{ NEGOTIATION_SENDS : has
    CLAUSE_PATTERNS
    CONTRACTS {
        uuid id PK
        string owner_id
        string filename
        text raw_text
        enum status "uploaded|processing|done|failed"
        timestamptz created_at
        timestamptz updated_at
    }
    ANALYSIS_RESULTS {
        uuid id PK
        uuid contract_id FK
        enum agent_type "risk_clause|tax_compliance|counter_draft"
        jsonb result_json
        text error
        timestamptz started_at
        timestamptz finished_at
    }
    CLAUSE_PATTERNS {
        uuid id PK
        string owner_id "owner or global or legacy"
        string pattern_name "clause_type:severity"
        text description
        text example_text
        string severity
        int times_matched
        float confidence
        bool is_active
    }
    NEGOTIATION_SENDS {
        uuid id PK
        uuid contract_id FK
        string recipient_email
        text counter_draft_text
        string status "stub|sent|failed"
        timestamptz sent_at
    }
```

- Enums are native PostgreSQL types (`contract_status`, `agent_type`); the codebase is
  PostgreSQL-specific (`UUID`, `JSONB`).
- `ClausePattern` has **no** `contract_id` — patterns are knowledge, not per-contract data.
  Superseded by phase 7: they are scoped by `owner_id`, not global. See the deltas section.
- Cascade deletes are declared both at ORM level (`cascade="all, delete-orphan"`) and DB level
  (`ondelete="CASCADE"`).
- There is **no uniqueness constraint** on `(contract_id, agent_type)` even though
  `_save_result` treats it as a key.

## 6. Self-improving skill store (`services/skill_store.py`)

- `get_active_patterns(db=None)` — opens its own session if none passed; returns active patterns
  ordered by `times_matched DESC`. Called by agent A on every run; failures degrade gracefully
  to `"No known patterns yet."`.
- `save_new_patterns(db, findings)` — for each finding with `confidence >= 0.75` (`CONFIDENCE_THRESHOLD`):
  - key is `pattern_name = f"{clause_type}:{severity}"`;
  - existing active row → `times_matched += 1`;
  - otherwise insert with `description = explanation[:500]`, `example_text = original_text[:500]`.
- `SIMILARITY_CHECK_FIELD` is declared but unused; matching is exact-string, not semantic.
- Patterns are injected into agent A's prompt as bullet lines truncated to 120 chars of example
  text — the context grows unbounded as the store fills up.

> Superseded by later phases: matching still is not semantic, but the context is capped at
> `max_rag_patterns` (40) and reads/writes are scoped by `owner_id`. See the deltas section.

## 7. HTTP surface

| Method | Path | Handler | Returns |
|---|---|---|---|
| GET | `/` | `routes_pages.index` | `upload.html` |
| GET | `/contracts/{id}` | `routes_pages.contract_page` | `result.html` (404 if missing) |
| GET | `/partials/{id}/status` | `routes_pages.partial_status` | `partials/status.html` |
| GET | `/partials/{id}/result` | `routes_pages.partial_result` | `partials/result.html` |
| POST | `/api/contracts/upload` | `routes_upload.upload_contract` | `ContractUploadResponse` |
| GET | `/api/contracts/{id}/status` | `routes_analysis.get_contract_status` | `ContractStatusResponse` |
| GET | `/api/contracts/{id}/result` | `routes_analysis.get_contract_result` | `ContractResultResponse` |
| POST | `/api/contracts/{id}/send` | `routes_negotiate.send_negotiation` | `NegotiationSendResponse` |
| GET | `/docs`, `/openapi.json` | FastAPI default | Swagger UI / schema |

Notes: routers are mounted with `prefix="/api"` in `main.py`, so the paths above are final.
`/partials/...` are not under `/api` because `routes_pages` is included without a prefix.

> Superseded by phase 6: `/health` and `/health/ready` exist, and every route except those
> two and `/static/*` passes through rate limiting, the optional `ACCESS_TOKEN` gate, and
> owner-scoped contract lookup. See the deltas section.

## 8. Frontend map

- `base.html` — sticky nav, footer disclaimer ("Bukan nasihat hukum"), loads HTMX 1.9.12
  (SRI-pinned) and Alpine 3.14.1 from unpkg, Google Fonts (Inter + JetBrains Mono), `/static/app.css`,
  then `/static/app.js`.
- `upload.html` — `x-data="uploadApp()"`: drag & drop zone syncing a hidden file input via
  `DataTransfer`, submit button gated on `fileName`, toast notifications, three feature pills.
- `result.html` — `x-data="resultApp(id)"`: header with filename + contract UUID, plus the two
  HTMX polling containers and a toast slot.
- `partials/status.html` — four states (`done` / `failed` / `processing` / else-waiting) rendered
  as `.sb` status banners; the non-terminal states re-poll themselves via `hx-swap="outerHTML"`.
- `partials/result.html` — renders risk findings, tax findings, and the counter-draft card
  (`x-data="sendApp(id)"`, textarea + clipboard copy + email send). **This file is currently
  broken** — see `KNOWN_ISSUES.md` #1.
- `app.css` — CSS-variable dark theme; key classes: `.card`, `.card-hd`, `.fc` (finding card with
  severity stripe `.fc-sc/.fc-sh/.fc-sm/.fc-sl`), `.badge-{severity}`, `.sb-*`, `.alert-*`, `.toast-*`,
  `.spinner`, `.htmx-indicator`.
- `app.js` — three Alpine components: `uploadApp`, `resultApp`, `sendApp`.

Severity → CSS coupling: `partials/result.html` builds the stripe class from
`f.severity[0]`, so severity strings must start with `c`, `h`, `m`, or `l`.

## 9. Configuration

`app/config.py › Settings` (pydantic-settings, `.env`, case-insensitive):

| Field | Env var | Default |
|---|---|---|
| `database_url` | `DATABASE_URL` | `postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield` |
| `redis_url` | `REDIS_URL` | `redis://redis:6379/0` |
| `hermes_base_url` | `HERMES_BASE_URL` | `https://hermes-agent.nousresearch.com` |
| `hermes_api_key` | `HERMES_API_KEY` | `changeme` |
| `llm_model` | `LLM_MODEL` | `NousResearch/Hermes-3-Llama-3.1-70B` |
| `default_locale` | `DEFAULT_LOCALE` | `id-ID` |

`llm_client.get_llm_client()` appends `/v1` to `hermes_base_url`, so the env value must **not**
already include `/v1` (contradicts the README's Ollama tip — see `KNOWN_ISSUES.md` #6).
`default_locale` is never read anywhere.

`docker-compose.yml`: both `api` and `worker` build from `./backend`, bind-mount `./backend:/app`
(so `pip install .` inside the image is shadowed by the mount), and require `.env` via `env_file`.
`api` runs uvicorn with `--reload`; `postgres` publishes 5432 and `redis` 6379 to the host.

## 10. Migrations

- `alembic/env.py` swaps `postgresql+asyncpg` → `postgresql+psycopg2` and injects the URL from
  `Settings`, so `alembic upgrade head` works with the same `.env`.
- `0001_initial_schema.py` creates the four tables plus `ix_analysis_results_contract_id` and
  `ix_contracts_status`.
- **Migrations are not run by Compose.** `main.py`'s lifespan calls `Base.metadata.create_all`,
  which creates tables (without the two indexes) on first API boot. See `KNOWN_ISSUES.md` #2.

## 11. Dependencies (`backend/pyproject.toml`)

Runtime: `fastapi==0.111.1`, `uvicorn[standard]==0.30.1`, `sqlalchemy[asyncio]==2.0.31`,
`asyncpg==0.29.0`, `psycopg2-binary==2.9.9`, `alembic==1.13.2`, `langchain==0.2.11`,
`langchain-openai==0.1.19`, `openai==1.35.13`, `pypdf==4.3.1`, `redis==5.0.8`, `rq==1.16.2`,
`pydantic-settings==2.3.4`, `jinja2==3.1.4`, `python-multipart==0.0.9`, `httpx==0.27.0`,
`python-dotenv==1.0.1`.

Dev extra: `pytest==8.2.2`, `pytest-asyncio==0.23.8`, `httpx==0.27.0`, `ruff==0.5.5`.

All pins are exact. `langchain` / `langchain-openai` are declared but **never imported** —
the agents call the `openai` SDK directly. `pytest` config points at `testpaths = ["tests"]`,
which does not exist.

## 12. Seed data

| File | Records | Shape | Wired in? |
|---|---|---|---|
| `sample_contract.txt` | — | Indonesian freelance contract with intentionally abusive Pasal 1–N (non-compete 5y ASEAN-wide, total IP assignment, 60-day payment with unilateral withholding) | Manually uploaded in the demo |
| `dataset1.json` | 100 | `{id, category, severity, industry, party, contract_snippet, risk_findings[], tax_findings[], counter_suggestion, source_reference, applicable_law, locale}` | **Yes** — `services/seed_loader.py` |
| `datasetpasal1.json` | 50 | `{id, kode, nama_lengkap, jenis, pasal_relevan[], topik, relevansi_kontrak, url_resmi, url_pdf, instansi, status, catatan_pasal{}}` | **Yes** — `services/seed_loader.py` |

`dataset1.json` maps almost 1:1 onto `clause_patterns` and bootstraps the skill store via
`python -m app.seed`; `datasetpasal1.json` backs the citation list injected into
`prompts/tax_compliance.txt`.

## 13. Extension points

| Goal | Where to hook |
|---|---|
| Bootstrap the skill store from `seed/dataset1.json` | done — `services/seed_loader.py`, CLI in `app/seed.py` |
| Add legal-citation RAG for tax agent | done — `legal_references_text()` → `{{ legal_references }}` |
| Real email delivery | replace `services/mailer.py` body, set `NegotiationSend.status="sent"` |
| Multi-tenancy / auth | done (anonymous, cookie-scoped) — `app/security.py`, `app/middleware.py`, `app/api/deps.py`. A real account system replaces `owner_id` with a `users` FK |
| Add a 4th agent | new `agents/agent_*.py`, new `AgentType` enum member + migration, wire into `orchestrator.run_analysis`, render in `partials/result.html` |
| Replace polling with push | SSE/WebSocket endpoint in `routes_pages.py`, drop `hx-trigger="every ..."` |
| Retry failed agents | `llm_client.chat_completion_json` already retries JSON failures; re-enqueue in `queue.py` for whole-job retries |

---

## Since `d8e310b` — behavioural deltas

What changed relative to sections 2–12 above:

**Schema ownership.** `main.py` no longer calls `Base.metadata.create_all`. A one-shot
`migrate` service runs `alembic upgrade head` then `python -m app.seed`, and both `api` and
`worker` gate on `service_completed_successfully`. A broken migration now fails at boot
instead of being masked.

> Running `alembic upgrade head` from both the `api` and `worker` commands looked simpler
> but raced on a fresh database: both tried to `CREATE TABLE alembic_version` and the loser
> died on `duplicate key value violates unique constraint "pg_type_typname_nsp_index"`. The
> race only appears on the very first boot of an empty volume, which is why it survived
> several restarts before being caught.

**Sessions.** `db/session.py` no longer exposes a module-level `engine`. Engines and
sessionmakers are keyed by `id(asyncio.get_running_loop())`, because RQ runs each job in a
fresh `asyncio.run` and asyncpg connections cannot cross loops. Call `dispose_engine()` when
a loop is about to end. `AsyncSessionLocal()` still works as before at every call site.

**LLM calls.** Agents call `chat_completion_json(...)`, not `chat_completion` +
`json.loads`. It requests native JSON mode, falls back when the provider rejects
`response_format`, tolerates fences and surrounding prose, and re-prompts on parse failure.
`hermes_base_url` may include or omit `/v1`.

**Agent contract.** All three agents return `AgentRun(result, started_at, finished_at)` — a
NamedTuple defined in `agent_risk_clause.py` — rather than stuffing `_started_at` into the
payload. Agent C runs even when A or B failed, receiving `EMPTY_FINDINGS` in place of the
missing input. The contract ends `done` if any agent produced output.

**Persistence.** `_save_result` is a real `ON CONFLICT` upsert against
`uq_analysis_results_contract_agent`, so re-running an analysis updates the three existing
rows instead of adding more.

**Skill store.** Dedupe key is `fingerprint(clause_type, example_text)` — first 32 chars of
sha256 over the clause type plus whitespace-normalised lowercased example — not
`clause_type:severity`. `get_active_patterns()` takes a `limit` (default 40) and an
`owner_id`; see the phase 7 note below for the scoping rules.

**Upload.** `routes_upload.py` delegates to `services/upload_validation.py`: extension,
declared content type, `Content-Length`, streaming read with a hard cap, `%PDF-` magic-byte
sniffing, and binary detection before the latin-1 fallback. A PDF named `.txt` is routed to
the PDF extractor rather than rejected. Enqueue failure marks the contract `failed` and
returns 503.

**New columns.** `contracts.job_id`, `contracts.error` (surfaced in `partials/status.html`
when status is `failed`), and an `ix_contracts_status_updated_at` index for the reaper.

**Front-end.** htmx and Alpine load from `/static/vendor/`, not unpkg. Font stacks in
`app.css` have system fallbacks and no template declares `font-family` inline.

**Configuration.** Ten new settings; see the table in `README.md`. `Settings` uses
`SettingsConfigDict` with `extra="ignore"`, so an unrecognised `.env` key no longer crashes
startup.

**Access control (phase 6).** Three middlewares in `app/middleware.py`, registered in
`main.py` so the per-request order is rate limit → access gate → owner identity (Starlette
applies `add_middleware` in reverse, so the registration order is the inverse).

- `app/security.py` — HMAC-SHA256 cookie signing over `SECRET_KEY`, anonymous 32-hex owner
  ids, constant-time token comparison. With no `SECRET_KEY` a random per-process key is
  generated and a warning is logged.
- `app/services/rate_limit.py` — fixed-window counters in Redis (`INCR` + `EXPIRE` on a
  window-suffixed key), **fail-open** to a bounded in-memory dict when Redis is down.
- `contracts.owner_id` (migration `0004`, `NOT NULL`, indexed) is the authorization key.
  Pre-existing rows are backfilled to the sentinel `legacy`, which `new_owner_id()` can
  never produce, so they are retained but unreachable.
- `app/api/deps.py::load_owned_contract` is the **only** place the ownership rule lives.
  Every contract read goes through it; `tests/test_security.py` fails if a route
  reintroduces a bare `select(Contract)`. A foreign or missing contract both yield 404.
- The HTMX partials catch the dependency's `HTTPException` and return an HTML fragment,
  because htmx swaps the response body into the page and a JSON error blob would land
  inside the results card.
- `/health*` and `/static/*` bypass the gate and the limiter: an orchestrator cannot present
  a credential, and gating liveness turns a credential mistake into a restart loop.
- CORS middleware is only added when `ALLOWED_ORIGINS` is non-empty.

`scripts/e2e_access_check.py` exercises all of this against a running stack (stdlib only,
exits non-zero on failure). It is not in the pytest suite because the suite must stay
runnable with `--no-deps`.

**Per-tenant skill store (phase 7).** `clause_patterns.owner_id` (migration `0005`,
`NOT NULL`, indexed) contains the poisoning half of #6. The unique key moved from
`(fingerprint)` to `(owner_id, fingerprint)` — two owners observing the same clause each
need their own row, or the second `INSERT` collides with a row it cannot see.

- `GLOBAL_OWNER = "global"` — curated `seed/dataset1.json` data. Read by everyone, written
  by nobody at runtime.
- `LEGACY_OWNER = "legacy"` — learned before ownership existed. Retained for auditing,
  never read: there is no way to know which rows came from a hostile upload. Migration
  `0005` classifies existing rows by `times_matched = 0` (only seeding sets that).
- Neither sentinel is reachable as a real owner: `new_owner_id()` always returns 32 hex
  characters. `readable_owners()` also refuses to widen access when handed a sentinel.
- `save_new_patterns(..., owner_id=...)` returns 0 rather than writing when the owner is
  missing or reserved, and never bumps a matching `global` row's counter — that would be a
  runtime write to shared state, letting one user reorder everyone's RAG ordering.
- `get_active_patterns()` reads `own + global`, over-fetches `limit * 2`, and dedupes by
  fingerprint preferring the private row, so a privately re-observed seeded clause does not
  occupy two of the 40 prompt slots.
- `orchestrator.run_analysis` reads `contract.owner_id` and threads it into both the risk
  agent and `_persist_agent`. A test fails if that wiring disappears.

**Integration tests (phase 8).** `tests/integration/` runs against a real PostgreSQL, so the
things whose bugs live in SQL are no longer verified only by hand.

- The fixture creates `<dbname>_test`, runs `alembic upgrade head` on it, and drops it at
  session end. `DATABASE_URL` is swapped for the session and `get_settings.cache_clear()` is
  called on both sides, so the app's own settings follow.
- **Skipped, not failed, when PostgreSQL is unreachable.** `--no-deps` is the documented fast
  path (417 unit tests in ~18s) and must stay green; `docker compose run --rm test` without
  `--no-deps` runs all 480.
- `clean_tables` truncates *before* each test, so a failure leaves its rows for inspection,
  and calls `dispose_engine()` after — pytest-asyncio gives each test a new loop and
  `db/session.py` keys engines by loop id, so skipping it leaks a pool per test.
- `test_db_schema.py` compares model columns against `information_schema` (catching drift a
  migration forgot), asserts every named index and constraint really exists, and round-trips
  `downgrade`/`upgrade` for the two newest revisions.
- `TestFingerprintBackfillParity` executes migration `0002`'s backfill SQL and compares it to
  `skill_store.fingerprint()` for seven inputs including whitespace, unicode and NULL. A
  divergence there is silent and permanent: backfilled rows would never match again.
- `TestPerLoopEngine` proves a *second* `asyncio.run` can actually query, which is the
  production failure the unit test only approximates by comparing engine identities.

**Production overlay (phase 9).** `docker-compose.prod.yml` is used as a second `-f`:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

It changes three things, each a correctness issue rather than a preference:

- Drops `./backend:/app`, so the running code is the built image rather than whatever is on
  the host disk. `volumes: !override` is required — Compose *merges* volume lists by default,
  so an ordinary redefinition would keep the inherited mount. `./seed:/app/seed:ro` stays,
  because that is data.
- Drops `--reload`. With no bind mount there is nothing to watch, and the reloader's
  supervisor process obscures crash exit codes.
- `ports: !override []` on `postgres` and `redis`. The base file publishes 5432 and 6379 with
  the credentials `legalshield:legalshield` and no Redis password; on a server that is a
  direct path in. Services reach each other over the compose network.

`--proxy-headers --forwarded-allow-ips=*` is deliberately **not** set: it makes uvicorn
rewrite `request.client.host` from `X-Forwarded-For`, which is the value the rate limiter
keys on, so a wildcard lets any caller mint a fresh identity per request. Behind a proxy,
name the proxy's IP explicitly and set `TRUST_PROXY_HEADERS=true`.

`tests/test_deployment_config.py` guards all of this by parsing both files. It strips `#`
comments before "this flag must not appear" assertions, because the overlay's comments
explain the flags they forbid.

> The compose files sit above the Docker build context (`./backend`), so the `test` service
> mounts them at **`/deploy`**, not under `/app`. A file mount nested inside the
> `./backend:/app` mount makes Docker create empty placeholder files on the host — that
> produced a stray `backend/deploy/` directory with two zero-byte files on the first attempt.

**Real SMTP and the end of inline styles (phase 10).**

`services/mailer.py` was a logging stub, so the "Kirim Draft" button reported success while
nothing left the process. It now speaks SMTP, with two shapes:

- **Stub mode remains the default.** An empty `SMTP_HOST` logs the envelope and returns
  success, so a checkout with no mail credentials behaves exactly as before and the test
  suite never opens a socket.
- **Live mode** builds a `EmailMessage` (plain-text body, the counter-draft as a `.txt`
  attachment) and hands it to `smtplib.SMTP` or `SMTP_SSL` depending on `SMTP_USE_SSL`,
  upgrading with `starttls()` when `SMTP_USE_TLS` is set.

Three constraints shape the interface, and each is load-bearing:

- **Failures are returned, never raised.** `routes_negotiate.py` persists a
  `negotiation_sends` row built from the outcome *before* it selects the HTTP status (502 on a
  delivery failure). An exception propagating out of the mailer would take the audit row with
  it, which is the one record that says a send was attempted.
- **`send_counter_draft` is `async` and wraps the blocking work in `asyncio.to_thread`.**
  `smtplib` blocks for the entire handshake — DNS, TCP, STARTTLS, AUTH — and the route runs on
  the event loop, so a slow relay would stall every other request in the process.
- **The SMTP error text is logged but withheld from the response.** `smtplib` messages quote
  the relay hostname and the authenticating username; the client gets a generic failure.

`valid_email()` additionally rejects CR and LF, which is what stops a crafted recipient from
injecting extra headers into the envelope. `config.py` gained `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_FROM`,
`SMTP_FROM_NAME` and `SMTP_TIMEOUT_SECONDS`.

The same phase closed #27. Every `style=` and `:style=` attribute is gone from all five
templates, replaced by roughly 60 classes in `app.css`; the dropzone's drag and selected
states use Alpine's `:class="{ 'is-dragover': ..., 'has-file': ... }"` object syntax rather
than interpolated CSS text. The reason is behavioural, not aesthetic: an inline attribute
cannot be overridden by any stylesheet rule and never consults the theme variables, so
spacing and colour changes could not reach the elements that carried the most styling.

Two guards in `tests/test_static_assets.py` prevent a relapse — no template may contain
`style=` or `:style=`, and every class a template references must be defined in `app.css`.
The second guard's `EXTERNAL` allow-list is deliberately just `{"htmx-indicator"}`; an early
broader list silently masked genuinely undefined classes.

> Visual confirmation of the CSS refactor is **unverified by the agent** — a screenshot was
> taken but could not be interpreted. The two guard tests are the safety net; a human should
> still eyeball `/` and a finished result page once.
