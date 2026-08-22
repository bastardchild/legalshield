"""
Shared test fixtures.

Environment defaults are set before any `app.*` import so that `app.config.Settings`
never picks up a developer's real `.env` values and no test can reach a live LLM.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield")
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
os.environ.setdefault("HERMES_BASE_URL", "http://llm.invalid")
os.environ.setdefault("HERMES_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")

from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

TEMPLATE_DIR = "app/templates"

ALL_TEMPLATES = [
    "base.html",
    "upload.html",
    "result.html",
    "partials/status.html",
    "partials/result.html",
]


@pytest.fixture
def jinja_env() -> Environment:
    """Template environment mirroring FastAPI's Jinja2Templates setup."""
    return Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)


@pytest.fixture
def strict_jinja_env() -> Environment:
    """
    Same, but raises on any undefined variable access.

    Used to prove the result partial tolerates malformed LLM findings instead of
    500-ing the whole page (KNOWN_ISSUES #14).
    """
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,
        undefined=StrictUndefined,
    )


def make_contract(status: str, filename: str = "kontrak.pdf") -> SimpleNamespace:
    """Minimal stand-in for a Contract ORM row (templates only read .status/.filename/.updated_at)."""
    now = datetime(2026, 8, 22, 14, 30, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        filename=filename,
        created_at=now,
        updated_at=now,
    )


def make_row(result_json: dict | None = None, error: str | None = None) -> SimpleNamespace:
    """Minimal stand-in for an AnalysisResult ORM row."""
    return SimpleNamespace(result_json=result_json, error=error)


@pytest.fixture
def contract_factory():
    return make_contract


@pytest.fixture
def row_factory():
    return make_row
