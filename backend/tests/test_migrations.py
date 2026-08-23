"""
Migration guards (KNOWN_ISSUES #2, #3, #11).

`alembic upgrade head` used to fail on `CREATE TYPE IF NOT EXISTS`, which PostgreSQL does
not support. The failure was invisible because `main.py` bootstrapped the schema with
`metadata.create_all` instead. These tests are static checks — they do not need a database,
so they run in the same unit pass as everything else.
"""
import io
import tokenize
from pathlib import Path

import pytest

from app.db.models import AnalysisResult, ClausePattern, Contract

VERSIONS_DIR = Path("alembic/versions")
MIGRATIONS = sorted(VERSIONS_DIR.glob("*.py"))


def _strip_comments(source: str) -> str:
    """
    Drop `#` comments so a comment explaining a forbidden pattern doesn't trip the check.

    Tokenising rather than regexing, so a `#` inside a SQL string literal survives.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return source
    return "\n".join(
        tok.string for tok in tokens if tok.type not in (tokenize.COMMENT, tokenize.NL)
    )


def _code_of(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def _all_migration_text() -> str:
    return "\n".join(_code_of(p) for p in MIGRATIONS)


def _squashed(text: str) -> str:
    """All whitespace removed, so token-level checks survive the tokeniser's re-joining."""
    return "".join(text.split())


def test_migrations_exist():
    assert MIGRATIONS, f"no migration files found in {VERSIONS_DIR}"


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_no_create_type_if_not_exists(path):
    """PostgreSQL has no IF NOT EXISTS for CREATE TYPE; it is a syntax error."""
    assert "CREATE TYPE IF NOT EXISTS" not in _code_of(path).upper()


def test_enum_creation_is_guarded():
    text = _all_migration_text().upper()
    assert "DUPLICATE_OBJECT" in text, "enum creation must tolerate pre-existing types"


def test_enum_columns_do_not_recreate_the_type():
    """Without create_type=False SQLAlchemy emits its own CREATE TYPE and the DO block conflicts."""
    assert "create_type=False" in _squashed(_all_migration_text())


def test_revision_chain_is_linear():
    revisions, downs = {}, {}
    for path in MIGRATIONS:
        scope: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("revision =", "down_revision =")):
                exec(stripped, {}, scope)  # noqa: S102 — literal assignment only
        revisions[path.name] = scope.get("revision")
        downs[path.name] = scope.get("down_revision")

    assert None not in revisions.values(), "every migration needs a revision id"
    assert list(downs.values()).count(None) == 1, "exactly one migration may be the root"
    known = set(revisions.values())
    for name, parent in downs.items():
        if parent is not None:
            assert parent in known, f"{name} points at unknown down_revision {parent!r}"


class TestModelConstraintsAreMigrated:
    """Constraints declared on the models must also exist in a migration."""

    @pytest.mark.parametrize(
        "constraint",
        [
            "uq_analysis_results_contract_agent",
            # Superseded by the per-owner constraint below, but still created by 0002 and
            # dropped by 0005, so the chain must keep mentioning it.
            "uq_clause_patterns_fingerprint",
            "uq_clause_patterns_owner_fingerprint",
            "ix_analysis_results_contract_id",
            "ix_contracts_status",
            "ix_contracts_owner_id",
            "ix_clause_patterns_owner_id",
        ],
    )
    def test_named_constraint_present_in_migrations(self, constraint):
        assert constraint in _all_migration_text()

    def test_upsert_constraint_declared_on_model(self):
        names = {c.name for c in AnalysisResult.__table__.constraints}
        assert "uq_analysis_results_contract_agent" in names

    def test_fingerprint_column_exists_and_is_required(self):
        col = ClausePattern.__table__.c.fingerprint
        assert col.nullable is False

    def test_fingerprint_is_backfilled_before_being_made_not_null(self):
        text = _all_migration_text()
        assert "sha256" in text, "existing rows need a backfill or the NOT NULL will fail"

    def test_duplicates_are_removed_before_unique_constraints(self):
        text = _all_migration_text().upper()
        assert "DELETE FROM ANALYSIS_RESULTS" in text
        assert "DELETE FROM CLAUSE_PATTERNS" in text

    def test_status_index_declared_on_model(self):
        assert "ix_contracts_status" in {i.name for i in Contract.__table__.indexes}

    def test_owner_scoped_constraints_are_declared_on_the_model(self):
        """A global unique constraint would reject a second owner's copy of the same clause."""
        names = {c.name for c in ClausePattern.__table__.constraints}
        assert "uq_clause_patterns_owner_fingerprint" in names
        assert "uq_clause_patterns_fingerprint" not in names

    def test_owner_columns_are_required(self):
        assert Contract.__table__.c.owner_id.nullable is False
        assert ClausePattern.__table__.c.owner_id.nullable is False


def test_app_does_not_create_schema_at_runtime():
    """create_all masked the broken migration and never created the indexes (#3)."""
    assert "create_all" not in _code_of(Path("app/main.py"))
