"""
Skill-store fingerprinting and tenant-scoping tests (KNOWN_ISSUES #17, #6).

The previous key was `clause_type:severity`, so every non-compete clause ever seen
collapsed into a single row and the first example was the only one retained. The store was
also global, which made a single successful prompt injection permanent and cross-tenant.
"""
import pytest

from app.services.skill_store import (
    CONFIDENCE_THRESHOLD,
    GLOBAL_OWNER,
    LEGACY_OWNER,
    RESERVED_OWNERS,
    fingerprint,
    readable_owners,
)


class TestFingerprint:
    def test_stable_across_calls(self):
        a = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor.")
        b = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor.")
        assert a == b

    def test_length_is_32(self):
        assert len(fingerprint("x", "y")) == 32

    def test_whitespace_and_case_insensitive(self):
        a = fingerprint("non_compete", "Dilarang  bekerja\nuntuk kompetitor.")
        b = fingerprint("non_compete", "dilarang bekerja untuk kompetitor.")
        assert a == b

    def test_leading_trailing_whitespace_ignored(self):
        assert fingerprint("t", "  teks  ") == fingerprint("t", "teks")

    def test_different_examples_differ(self):
        a = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor selama 5 tahun.")
        b = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor selama 1 tahun.")
        assert a != b

    def test_different_clause_types_differ(self):
        text = "Teks klausul yang sama persis."
        assert fingerprint("non_compete", text) != fingerprint("payment_terms", text)

    def test_empty_example_is_handled(self):
        assert len(fingerprint("other", "")) == 32
        assert fingerprint("other", "") == fingerprint("other", None)


def test_confidence_threshold_is_in_unit_range():
    """A threshold outside [0,1] would either store everything or nothing."""
    assert 0.0 < CONFIDENCE_THRESHOLD <= 1.0


class TestReadableOwners:
    """
    Which owners' patterns a caller may see. `legacy` rows were learned before ownership
    existed, so there is no way to tell which came from a hostile upload — never read them.
    """

    def test_own_plus_global(self):
        owners = readable_owners("a" * 32)
        assert set(owners) == {GLOBAL_OWNER, "a" * 32}

    def test_global_only_without_an_owner(self):
        assert readable_owners(None) == [GLOBAL_OWNER]
        assert readable_owners("") == [GLOBAL_OWNER]

    def test_legacy_is_never_readable(self):
        assert LEGACY_OWNER not in readable_owners(LEGACY_OWNER)
        assert LEGACY_OWNER not in readable_owners("b" * 32)

    @pytest.mark.parametrize("reserved", RESERVED_OWNERS)
    def test_reserved_ids_cannot_be_impersonated(self, reserved):
        """Passing a sentinel must not widen what the caller can read."""
        assert readable_owners(reserved) == [GLOBAL_OWNER]

    def test_sentinels_are_unreachable_as_real_owners(self):
        """new_owner_id() is 32 hex chars, so no real owner can collide with a sentinel."""
        from app.security import new_owner_id

        for _ in range(50):
            assert new_owner_id() not in RESERVED_OWNERS


class TestWriteScoping:
    """Writes must be attributable; the shared global bucket stays curated."""

    @pytest.mark.parametrize("owner", [None, "", GLOBAL_OWNER, LEGACY_OWNER])
    async def test_refuses_to_store_without_a_real_owner(self, owner):
        from app.services.skill_store import save_new_patterns

        finding = {
            "clause_type": "non_compete",
            "severity": "critical",
            "original_text": "Dilarang bekerja 5 tahun.",
            "explanation": "Tidak proporsional.",
            "confidence": 0.99,
        }
        # No DB is touched: the guard returns before any query, so None is a valid session.
        assert await save_new_patterns(None, [finding], owner_id=owner) == 0


class TestSeedDataIsGlobal:
    def test_seeded_rows_are_owned_by_the_global_bucket(self):
        from app.services.seed_loader import clause_patterns_from_dataset

        rows = clause_patterns_from_dataset()
        assert rows, "seed dataset produced no patterns"
        assert {r["owner_id"] for r in rows} == {GLOBAL_OWNER}

    def test_model_uniqueness_is_per_owner(self):
        """Two owners observing the same clause each need their own row."""
        from app.db.models import ClausePattern

        constraint = next(
            c for c in ClausePattern.__table__.constraints
            if c.name == "uq_clause_patterns_owner_fingerprint"
        )
        assert {c.name for c in constraint.columns} == {"owner_id", "fingerprint"}

    def test_owner_column_is_required(self):
        from app.db.models import ClausePattern

        assert ClausePattern.__table__.c.owner_id.nullable is False

    def test_orchestrator_passes_the_owner_through(self):
        """An unscoped call would silently fall back to global-only context."""
        from pathlib import Path

        src = Path("app/agents/orchestrator.py").read_text(encoding="utf-8")
        assert "owner_id=owner_id" in src
        assert "contract.owner_id" in src
