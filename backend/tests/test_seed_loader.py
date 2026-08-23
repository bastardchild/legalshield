"""
Seed-loader tests (KNOWN_ISSUES #18).

Both datasets shipped with the repository but nothing referenced them, so the risk agent
started with an empty RAG context and the tax agent had no citation list.
"""
import pytest

from app.services.seed_loader import (
    CLAUSE_DATASET,
    LEGAL_DATASET,
    SEED_TIMES_MATCHED,
    _seed_path,
    clause_patterns_from_dataset,
    legal_references,
    legal_references_text,
)
from app.services.findings import VALID_SEVERITIES
from app.services.skill_store import fingerprint


class TestDatasetsArePresent:
    @pytest.mark.parametrize("name", [CLAUSE_DATASET, LEGAL_DATASET])
    def test_file_is_mounted(self, name):
        assert _seed_path(name).exists(), (
            f"{name} is missing — check the seed bind mount in docker-compose.yml"
        )


class TestClausePatternsFromDataset:
    @pytest.fixture(scope="class")
    def rows(self):
        return clause_patterns_from_dataset()

    def test_produces_patterns(self, rows):
        assert len(rows) >= 90, f"expected ~100 seed patterns, got {len(rows)}"

    def test_fingerprints_are_unique(self, rows):
        fps = [r["fingerprint"] for r in rows]
        assert len(fps) == len(set(fps))

    def test_fingerprint_matches_runtime_function(self, rows):
        """A drifting fingerprint would make the seed and the runtime store duplicate."""
        row = rows[0]
        clause_type = row["pattern_name"].rsplit(":", 1)[0]
        assert row["fingerprint"] == fingerprint(clause_type, row["example_text"])

    def test_severities_are_canonical(self, rows):
        assert {r["severity"] for r in rows} <= set(VALID_SEVERITIES)

    def test_confidence_in_unit_range(self, rows):
        assert all(0.0 <= r["confidence"] <= 1.0 for r in rows)

    def test_required_fields_are_non_empty(self, rows):
        for r in rows:
            assert r["pattern_name"]
            assert r["description"]
            assert r["example_text"]

    def test_column_limits_respected(self, rows):
        assert all(len(r["description"]) <= 500 for r in rows)
        assert all(len(r["example_text"]) <= 500 for r in rows)
        assert all(len(r["pattern_name"]) <= 256 for r in rows)

    def test_seeded_rows_rank_below_observed_ones(self, rows):
        """RAG context is ordered by times_matched, so real matches must outrank seeds."""
        assert SEED_TIMES_MATCHED == 0
        assert all(r["times_matched"] == 0 for r in rows)

    def test_covers_multiple_clause_types(self, rows):
        types = {r["pattern_name"].rsplit(":", 1)[0] for r in rows}
        assert len(types) >= 8

    def test_is_deterministic(self):
        assert clause_patterns_from_dataset() == clause_patterns_from_dataset()


class TestLegalReferences:
    def test_loads_records(self):
        assert len(legal_references()) >= 40

    def test_every_record_has_a_code(self):
        assert all(ref["kode"] for ref in legal_references())

    def test_pasal_is_a_list_of_strings(self):
        for ref in legal_references():
            assert isinstance(ref["pasal"], list)
            assert all(isinstance(p, str) for p in ref["pasal"])

    def test_text_is_capped(self):
        assert len(legal_references_text(limit=5).splitlines()) == 5

    def test_text_mentions_known_regulations(self):
        text = legal_references_text(limit=50)
        assert "KUHPerdata" in text

    def test_text_is_non_empty(self):
        assert legal_references_text().strip()


class TestTaxPromptWiring:
    def test_prompt_has_a_references_placeholder(self):
        from pathlib import Path

        template = Path("app/agents/prompts/tax_compliance.txt").read_text(encoding="utf-8")
        assert "{{ legal_references }}" in template

    def test_agent_fills_both_placeholders(self):
        from app.agents.agent_tax_compliance import _load_prompt

        out = _load_prompt("ISI-KONTRAK", "DAFTAR-HUKUM")
        assert "ISI-KONTRAK" in out
        assert "DAFTAR-HUKUM" in out
        assert "{{" not in out
