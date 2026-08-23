"""
Migration-chain integration tests.

Everything here was previously verified only by hand against a live stack. The static
guards in `test_migrations.py` prove the migrations *say* the right things; these prove
PostgreSQL actually accepts them and that the resulting schema matches the models.

The one that matters most is `TestFingerprintBackfillParity`: migration `0002` backfills
`clause_patterns.fingerprint` with SQL that reimplements `skill_store.fingerprint()`. The
two are compared byte-for-byte here, because a divergence would silently split every
pre-existing pattern into a duplicate the runtime code can never match again.
"""
import pytest

from tests.integration.conftest import alembic_downgrade, alembic_head, alembic_upgrade

pytestmark = pytest.mark.integration


def _columns(conn, table: str) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            """,
            (table,),
        )
        return {
            row[0]: {"nullable": row[1] == "YES", "type": row[2]} for row in cur.fetchall()
        }


def _constraints(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


def _indexes(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,))
        return {row[0] for row in cur.fetchall()}


def _enum_labels(conn, name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.enumlabel
            FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = %s
            """,
            (name,),
        )
        return {row[0] for row in cur.fetchall()}


class TestSchemaMatchesModels:
    def test_alembic_is_at_head(self, sync_connection):
        with sync_connection.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            applied = cur.fetchone()[0]
        assert applied == alembic_head()

    @pytest.mark.parametrize(
        "table", ["contracts", "analysis_results", "clause_patterns", "negotiation_sends"]
    )
    def test_table_exists(self, sync_connection, table):
        assert _columns(sync_connection, table), f"{table} was not created"

    @pytest.mark.parametrize(
        ("table", "model_name"),
        [
            ("contracts", "Contract"),
            ("analysis_results", "AnalysisResult"),
            ("clause_patterns", "ClausePattern"),
            ("negotiation_sends", "NegotiationSend"),
        ],
    )
    def test_no_column_drift(self, sync_connection, table, model_name):
        """A column added to a model without a migration is invisible until a query fails."""
        from app.db import models

        model = getattr(models, model_name)
        declared = {c.name for c in model.__table__.columns}
        actual = set(_columns(sync_connection, table))
        assert declared == actual, (
            f"{table}: only in model {declared - actual}, only in database {actual - declared}"
        )

    @pytest.mark.parametrize(
        ("table", "column"),
        [("contracts", "owner_id"), ("clause_patterns", "owner_id")],
    )
    def test_owner_columns_are_not_null(self, sync_connection, table, column):
        assert _columns(sync_connection, table)[column]["nullable"] is False

    def test_upsert_constraint_exists(self, sync_connection):
        """The orchestrator's ON CONFLICT names this constraint; without it every upsert 500s."""
        assert "uq_analysis_results_contract_agent" in _constraints(
            sync_connection, "analysis_results"
        )

    def test_clause_pattern_uniqueness_is_per_owner(self, sync_connection):
        constraints = _constraints(sync_connection, "clause_patterns")
        assert "uq_clause_patterns_owner_fingerprint" in constraints
        assert "uq_clause_patterns_fingerprint" not in constraints, (
            "the global constraint would reject a second owner's copy of the same clause"
        )

    @pytest.mark.parametrize(
        ("table", "index"),
        [
            ("contracts", "ix_contracts_status"),
            ("contracts", "ix_contracts_status_updated_at"),
            ("contracts", "ix_contracts_owner_id"),
            ("analysis_results", "ix_analysis_results_contract_id"),
            ("clause_patterns", "ix_clause_patterns_owner_id"),
        ],
    )
    def test_index_exists(self, sync_connection, table, index):
        """create_all never made these; only the migrations do (KNOWN_ISSUES #3)."""
        assert index in _indexes(sync_connection, table)

    def test_enums_have_the_expected_labels(self, sync_connection):
        assert _enum_labels(sync_connection, "contract_status") == {
            "uploaded", "processing", "done", "failed",
        }
        assert _enum_labels(sync_connection, "agent_type") == {
            "risk_clause", "tax_compliance", "counter_draft",
        }

    def test_analysis_results_cascade_on_contract_delete(self, sync_connection):
        """ondelete=CASCADE at the DB level, not only in the ORM relationship."""
        with sync_connection.cursor() as cur:
            cur.execute(
                """
                SELECT confdeltype FROM pg_constraint
                WHERE conrelid = 'analysis_results'::regclass AND contype = 'f'
                """
            )
            assert {row[0] for row in cur.fetchall()} == {"c"}


class TestFingerprintBackfillParity:
    """
    Migration 0002's SQL must produce exactly what `skill_store.fingerprint()` produces.

    A divergence is silent and permanent: backfilled rows would never match again, so every
    pre-existing pattern would be duplicated on its next observation.
    """

    SQL = """
    SELECT substr(
        encode(
            sha256(
                convert_to(
                    split_part(%s, ':', 1)
                    || '|'
                    || lower(btrim(regexp_replace(coalesce(%s, ''), '\\s+', ' ', 'g'))),
                    'UTF8'
                )
            ),
            'hex'
        ),
        1, 32
    )
    """

    @pytest.mark.parametrize(
        ("pattern_name", "example_text"),
        [
            ("non_compete:critical", "Dilarang bekerja untuk kompetitor selama 5 tahun."),
            ("payment_terms:high", "  Pembayaran   60 hari\nsetelah invoice.  "),
            ("ip_assignment:critical", "SELURUH HKI DIALIHKAN."),
            ("other:medium", ""),
            ("tax:low", "Pasal 23 — pemotongan 2%"),
            ("unicode:medium", "Klausul dengan é, ü, dan 中文."),
            ("multi:high", "a\t\tb\r\nc   d"),
        ],
    )
    def test_sql_matches_python(self, sync_connection, pattern_name, example_text):
        from app.services.skill_store import fingerprint

        with sync_connection.cursor() as cur:
            cur.execute(self.SQL, (pattern_name, example_text))
            sql_result = cur.fetchone()[0]

        clause_type = pattern_name.split(":")[0]
        assert sql_result == fingerprint(clause_type, example_text)

    def test_null_example_text_matches_python(self, sync_connection):
        from app.services.skill_store import fingerprint

        with sync_connection.cursor() as cur:
            cur.execute(self.SQL, ("other:medium", None))
            assert cur.fetchone()[0] == fingerprint("other", "")


class TestDowngradeIsReversible:
    """
    Each newest migration must be reversible, or a bad deploy cannot be rolled back.

    The database is restored to head in `finally` regardless of outcome, so a failure here
    does not cascade into every later test.
    """

    def _revision(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]

    def test_0005_round_trip(self, sync_connection):
        try:
            alembic_downgrade("0004")
            sync_connection.rollback()  # see the new state, not the snapshot we started with
            assert self._revision(sync_connection) == "0004"
            constraints = _constraints(sync_connection, "clause_patterns")
            assert "uq_clause_patterns_fingerprint" in constraints
            assert "owner_id" not in _columns(sync_connection, "clause_patterns")
        finally:
            alembic_upgrade("head")

        sync_connection.rollback()
        assert "owner_id" in _columns(sync_connection, "clause_patterns")

    def test_0004_round_trip(self, sync_connection):
        try:
            alembic_downgrade("0003")
            sync_connection.rollback()
            assert self._revision(sync_connection) == "0003"
            assert "owner_id" not in _columns(sync_connection, "contracts")
        finally:
            alembic_upgrade("head")

        sync_connection.rollback()
        assert _columns(sync_connection, "contracts")["owner_id"]["nullable"] is False
