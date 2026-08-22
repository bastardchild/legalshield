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
- **Frontend**: Jinja2 + HTMX polling + Alpine.js, no build step
- **Infra**: Docker Compose (`postgres`, `redis`, `api`, `worker`)
- **Repo state**: single commit (`d8e310b init commit`), no tests, no CI

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
- Frontend styling: shared classes in `app/static/app.css`, plus heavy inline `style="..."`
  in templates. Alpine components are registered in `app/static/app.js` under `alpine:init`.

## Working agreements

1. **Check `KNOWN_ISSUES.md` first.** Several defects are already diagnosed; do not re-debug them.
2. **Do not "fix" unrelated issues** while working a task — add them to `KNOWN_ISSUES.md` instead.
3. **Schema changes touch two places**: `app/db/models.py` *and* `alembic/versions/`.
   Note that `main.py` currently runs `Base.metadata.create_all` at startup, so Alembic is
   effectively bypassed (see `KNOWN_ISSUES.md` #2).
4. **Never commit `.env`.** Only `.env.example` is tracked; `.gitignore` covers `.env`.
5. **Validation available locally without Docker**:
   ```sh
   python -c "import ast,pathlib;[ast.parse(p.read_text(encoding='utf-8'),str(p)) for p in pathlib.Path('app').rglob('*.py')]"
   python -c "import jinja2;e=jinja2.Environment(loader=jinja2.FileSystemLoader('app/templates'));[e.get_template(t) for t in ['base.html','upload.html','result.html','partials/status.html','partials/result.html']]"
   ```
   Full end-to-end requires `docker-compose up --build` plus a working `HERMES_API_KEY`.
6. **Update memory files** when you change architecture, add a service, or resolve/introduce
   an issue. Keep entries short and evidence-backed (file + line).

## Quick task → file lookup

| I want to… | Go to |
|---|---|
| Change how risky clauses are detected | `app/agents/prompts/risk_clause.txt`, `app/agents/agent_risk_clause.py` |
| Change tax/compliance rules | `app/agents/prompts/tax_compliance.txt` |
| Change counter-draft output shape | `app/agents/prompts/counter_draft.txt`, `app/agents/agent_counter_draft.py` |
| Change agent sequencing / failure handling | `app/agents/orchestrator.py` |
| Change the self-improving pattern store | `app/services/skill_store.py` |
| Add/modify an API endpoint | `app/api/routes_*.py` + `app/schemas.py` |
| Change the results UI | `app/templates/partials/result.html`, `app/static/app.css` |
| Change polling behaviour | `app/templates/result.html`, `app/static/app.js` |
| Swap LLM provider | `.env` (`HERMES_BASE_URL`), `app/services/llm_client.py` |
| Add a table/column | `app/db/models.py` + `alembic/versions/` |
