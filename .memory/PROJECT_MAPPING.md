# PROJECT MAPPING — LegalShield Agent

Structural map of the repository as it exists at commit `d8e310b`.
Line references are approximate anchors, not guarantees.

## 1. Repository tree (actual)

```
C:\legalshield\
├── .env.example                  # env template (git-tracked; .env is ignored)
├── .gitignore
├── docker-compose.yml            # postgres, redis, api, worker
├── README.md                     # user-facing docs (Bahasa Indonesia)
├── .memory/                      # ← agent memory (this directory)
├── seed/
│   ├── sample_contract.txt       # 3.9 KB deliberately abusive freelance contract (demo input)
│   ├── dataset1.json             # 100 labelled risky-clause records (NOT wired into code)
│   └── datasetpasal1.json        # 50 Indonesian legal-reference records (NOT wired into code)
└── backend/
    ├── Dockerfile                # python:3.11-slim, installs project from pyproject.toml
    ├── pyproject.toml            # hatchling; deps + ruff + pytest config
    ├── alembic.ini
    ├── alembic/
    │   ├── env.py                # rewrites asyncpg URL → psycopg2 for migrations
    │   ├── script.py.mako
    │   └── versions/0001_initial_schema.py
    └── app/
        ├── __init__.py
        ├── main.py               # FastAPI app, lifespan, static mount, router wiring
        ├── config.py             # pydantic-settings Settings + cached get_settings()
        ├── schemas.py            # Pydantic request/response models
        ├── worker.py             # RQ worker entrypoint (`python -m app.worker`)
        ├── worker_async.py       # unused helper (dead code)
        ├── db/
        │   ├── session.py        # async engine, AsyncSessionLocal, Base, get_db()
        │   └── models.py         # Contract, AnalysisResult, ClausePattern, NegotiationSend
        ├── api/
        │   ├── routes_pages.py   # HTML pages + HTMX partials
        │   ├── routes_upload.py  # POST /api/contracts/upload
        │   ├── routes_analysis.py# status + result JSON
        │   └── routes_negotiate.py# POST /api/contracts/{id}/send
        ├── agents/
        │   ├── orchestrator.py   # A+B parallel → C sequential, persistence, status
        │   ├── agent_risk_clause.py
        │   ├── agent_tax_compliance.py
        │   ├── agent_counter_draft.py
        │   └── prompts/{risk_clause,tax_compliance,counter_draft}.txt
        ├── services/
        │   ├── llm_client.py     # AsyncOpenAI factory + chat_completion()
        │   ├── pdf_extractor.py  # pypdf text extraction
        │   ├── queue.py          # Redis/RQ enqueue + sync wrapper
        │   ├── skill_store.py    # self-improving clause pattern store (RAG context)
        │   └── mailer.py         # stub "send" (log only)
        ├── templates/
        │   ├── base.html         # nav/footer, HTMX + Alpine CDN, fonts
        │   ├── upload.html       # drag & drop upload, HTMX POST
        │   ├── result.html       # shell with two polling containers
        │   └── partials/{status.html,result.html}
        └── static/{app.css,app.js}
```

> `README.md` documents `backend/requirements.txt` — that file does not exist; dependencies
> live in `pyproject.toml`. It also omits `seed/dataset1.json` and `seed/datasetpasal1.json`.

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
- `ClausePattern` has **no** `contract_id` — patterns are global, cross-tenant knowledge.
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
There is **no** authentication, rate limiting, CORS config, or health endpoint.
`/partials/...` are not under `/api` because `routes_pages` is included without a prefix.

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
| `dataset1.json` | 100 | `{id, category, severity, industry, party, contract_snippet, risk_findings[], tax_findings[], counter_suggestion, source_reference, applicable_law, locale}` | **No** — no loader exists |
| `datasetpasal1.json` | 50 | `{id, kode, nama_lengkap, jenis, pasal_relevan[], topik, relevansi_kontrak, url_resmi, url_pdf, instansi, status, catatan_pasal{}}` | **No** — no loader exists |

`dataset1.json` maps almost 1:1 onto `clause_patterns` and would be the natural bootstrap for the
skill store; `datasetpasal1.json` would back a legal-citation RAG for agent B.

## 13. Extension points

| Goal | Where to hook |
|---|---|
| Bootstrap the skill store from `seed/dataset1.json` | new `services/seed_loader.py`, call from `main.py` lifespan or a CLI |
| Add legal-citation RAG for tax agent | load `datasetpasal1.json`, inject into `prompts/tax_compliance.txt` |
| Real email delivery | replace `services/mailer.py` body, set `NegotiationSend.status="sent"` |
| Multi-tenancy / auth | new `users` table + FK on `contracts`, dependency in every router |
| Add a 4th agent | new `agents/agent_*.py`, new `AgentType` enum member + migration, wire into `orchestrator.run_analysis`, render in `partials/result.html` |
| Replace polling with push | SSE/WebSocket endpoint in `routes_pages.py`, drop `hx-trigger="every ..."` |
| Retry failed agents | wrap `chat_completion` in `llm_client.py` or re-enqueue in `queue.py` |
