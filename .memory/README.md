# `.memory/` — Agent Working Memory

Persistent notes for AI coding agents (and humans) working on **LegalShield Agent**.
Read these before touching code; update them after landing meaningful changes.

## Files

| File | Purpose |
|---|---|
| `PROJECT_MAPPING.md` | Structural map: every file, what it does, how data flows, where to change what. |
| `KNOWN_ISSUES.md` | Verified bugs, risks, and tech debt with severity, evidence, and suggested fixes. |
| `README.md` | This file — conventions and workflow for using the memory directory. |

## Project at a glance

LegalShield Agent is a Bahasa-Indonesia-first SaaS MVP that analyses freelance/service
contracts (PDF or TXT) with three LLM sub-agents and produces a counter-draft.

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 asyncio + PostgreSQL 16
- **Queue**: Redis 7 + RQ (sync worker bridging into `asyncio.run`)
- **LLM**: any OpenAI-compatible endpoint via `openai.AsyncOpenAI` (default: Hermes / Nous Research)
- **Frontend**: Jinja2 + HTMX polling + Alpine.js, no build step, assets vendored locally
- **Infra**: Docker Compose (`postgres`, `redis`, `api`, `worker`, `test` behind a profile)
- **Repo state**: repaired through phases 1–5; 237 tests; verified end-to-end against a live
  LLM provider. See `KNOWN_ISSUES.md` for the status ledger.

Entry points: `backend/app/main.py` (API) and `backend/app/worker.py` (RQ worker).

## Conventions observed in the codebase

- Python 3.11+, 4-space indent, `ruff` config in `pyproject.toml` (line-length 100, `E,F,I,UP`, `E501` ignored).
- Module-level `logger = logging.getLogger(__name__)` in every module; agents log with a
  `[AgentName]` prefix, orchestrator with `[Orchestrator]`, skill store with `[SkillStore]`.
- All DB access is async. Request-scoped sessions come from `get_db()` (FastAPI dependency);
  background code opens its own `AsyncSessionLocal()` context per unit of work.
- Pydantic response models live in `app/schemas.py`; routers never return ORM objects directly.
- Agent prompts are plain-text files in `app/agents/prompts/` using `{{ placeholder }}`
  tokens replaced by `str.replace` (deliberately *not* Jinja).
- User-visible strings (UI, `explanation`, `recommendation`) are Bahasa Indonesia.
  Code, comments, and prompts are English.
- Frontend styling: shared classes in `app/static/app.css`, plus layout-related inline
  `style="..."` in templates. Inline `font-family` is banned (a test enforces this) — use the
  `.mono` class. Alpine components are registered in `app/static/app.js` under `alpine:init`.
- Tests live in `backend/tests/`, one module per concern, named after the subject rather than
  the issue number. Classes group related cases; each test asserts one thing. Where a fix is
  structural rather than behavioural (a migration, a template wiring), the test is a static
  guard that reads the file — see `test_migrations.py` and `test_static_assets.py`.

## Working agreements

1. **Check `KNOWN_ISSUES.md` first.** Several defects are already diagnosed; do not re-debug them.
2. **Do not "fix" unrelated issues** while working a task — add them to `KNOWN_ISSUES.md` instead.
3. **Schema changes touch two places**: `app/db/models.py` *and* `alembic/versions/`.
   Alembic owns the schema — `alembic upgrade head` runs in the container command, and
   `main.py` no longer calls `create_all`. `tests/test_migrations.py` fails if a constraint
   declared on a model has no matching migration.
4. **Never commit `.env`.** Only `.env.example` is tracked; `.gitignore` covers `.env`.
5. **Validation runs in Docker**, not on the host — host Python is 3.10 and lacks the
   dependencies:
   ```sh
   docker compose run --rm --no-deps test              # pytest (237 tests)
   docker compose run --rm --no-deps test ruff check .  # lint
   docker compose run --rm test alembic upgrade head    # needs postgres, so no --no-deps
   ```
   The `test` service pins `HERMES_BASE_URL=http://llm.invalid` inline, so no test can reach
   a real provider. Full end-to-end needs `docker compose up --build` and a working
   `HERMES_API_KEY`.
6. **Commit at the end of each phase**, with the reasoning in the body — why the old
   behaviour was wrong, not just what changed.
7. **Update memory files** when you change architecture, add a service, or resolve/introduce
   an issue. Keep entries short and evidence-backed (file + line).

## Quick task → file lookup

| I want to… | Go to |
|---|---|
| Change how risky clauses are detected | `app/agents/prompts/risk_clause.txt`, `app/agents/agent_risk_clause.py` |
| Change tax/compliance rules | `app/agents/prompts/tax_compliance.txt` |
| Change counter-draft output shape | `app/agents/prompts/counter_draft.txt`, `app/agents/agent_counter_draft.py` |
| Change agent sequencing / failure handling | `app/agents/orchestrator.py` |
| Change the self-improving pattern store | `app/services/skill_store.py` |
| Change how LLM output is cleaned up | `app/services/findings.py` |
| Change what uploads are accepted | `app/services/upload_validation.py` |
| Change stuck-contract handling | `app/services/reaper.py`, `app/config.py` |
| Change seeding or legal citations | `app/services/seed_loader.py`, `app/seed.py` |
| Add/modify an API endpoint | `app/api/routes_*.py` + `app/schemas.py` |
| Change the results UI | `app/templates/partials/result.html`, `app/static/app.css` |
| Change polling behaviour | `app/templates/result.html`, `app/static/app.js` |
| Swap LLM provider | `.env` (`HERMES_BASE_URL`), `app/services/llm_client.py` |
| Add a table/column | `app/db/models.py` + `alembic/versions/` |
| Upgrade htmx or Alpine | `app/static/vendor/`, `app/templates/base.html`, `tests/test_static_assets.py` |
