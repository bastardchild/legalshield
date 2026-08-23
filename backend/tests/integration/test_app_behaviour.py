"""
Application-behaviour integration tests, against a real database.

These cover the paths whose bugs are invisible to unit tests because they live in SQL:

* the orchestrator's `ON CONFLICT` upsert (re-running an analysis must update three rows,
  not accumulate them);
* owner-scoped reads and writes in the skill store, including the `global`/`legacy` sentinels;
* the reaper's single-statement `UPDATE ... RETURNING`;
* seed idempotency;
* the per-loop engine registry, which failed on a worker's *second* job in production but
  looks fine in any single-job test.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import AgentType, AnalysisResult, ClausePattern, Contract, ContractStatus
from tests.integration.conftest import owner_id

pytestmark = pytest.mark.integration


async def make_contract(
    db,
    owner: str,
    status: ContractStatus = ContractStatus.uploaded,
    text: str = "Pasal 1 — klausul berisiko.",
    updated_at: datetime | None = None,
) -> Contract:
    contract = Contract(
        id=uuid.uuid4(),
        owner_id=owner,
        filename="kontrak.txt",
        raw_text=text,
        status=status,
    )
    if updated_at is not None:
        contract.updated_at = updated_at
    db.add(contract)
    await db.commit()
    await db.refresh(contract)
    return contract


def finding(clause_type="non_compete", confidence=0.95, text="Dilarang bekerja 5 tahun."):
    return {
        "clause_type": clause_type,
        "severity": "critical",
        "original_text": text,
        "explanation": "Tidak proporsional.",
        "recommendation": "Batasi 12 bulan.",
        "confidence": confidence,
    }


class TestAnalysisResultUpsert:
    """
    `_save_result` uses ON CONFLICT against uq_analysis_results_contract_agent. Before that
    constraint existed a re-run inserted a second row and every later upsert raised
    MultipleResultsFound.
    """

    async def test_insert_then_update_keeps_one_row(self, db):
        from app.agents.orchestrator import _save_result

        contract = await make_contract(db, owner_id("a"))
        now = datetime.now(UTC)

        await _save_result(
            db, str(contract.id), AgentType.risk_clause, {"findings": []}, None, now, now
        )
        await _save_result(
            db, str(contract.id), AgentType.risk_clause,
            {"findings": [finding()]}, None, now, now,
        )

        rows = (
            await db.execute(
                select(AnalysisResult).where(AnalysisResult.contract_id == contract.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert len(rows[0].result_json["findings"]) == 1

    async def test_three_agents_produce_three_rows(self, db):
        from app.agents.orchestrator import _save_result

        contract = await make_contract(db, owner_id("a"))
        now = datetime.now(UTC)
        for agent in AgentType:
            await _save_result(db, str(contract.id), agent, {"ok": True}, None, now, now)

        count = (
            await db.execute(
                select(func.count(AnalysisResult.id)).where(
                    AnalysisResult.contract_id == contract.id
                )
            )
        ).scalar()
        assert count == 3

    async def test_rerun_updates_all_three_in_place(self, db):
        from app.agents.orchestrator import _save_result

        contract = await make_contract(db, owner_id("a"))
        now = datetime.now(UTC)
        for _ in range(3):
            for agent in AgentType:
                await _save_result(db, str(contract.id), agent, {"run": True}, None, now, now)

        count = (
            await db.execute(
                select(func.count(AnalysisResult.id)).where(
                    AnalysisResult.contract_id == contract.id
                )
            )
        ).scalar()
        assert count == 3

    async def test_error_is_cleared_on_a_successful_rerun(self, db):
        """A stale error next to fresh findings would render both in the results card."""
        from app.agents.orchestrator import _save_result

        contract = await make_contract(db, owner_id("a"))
        now = datetime.now(UTC)
        await _save_result(
            db, str(contract.id), AgentType.risk_clause, None, "provider timeout", None, None
        )
        await _save_result(
            db, str(contract.id), AgentType.risk_clause, {"findings": []}, None, now, now
        )

        row = (
            await db.execute(
                select(AnalysisResult).where(AnalysisResult.contract_id == contract.id)
            )
        ).scalar_one()
        assert row.error is None
        assert row.result_json == {"findings": []}

    async def test_deleting_a_contract_cascades(self, db):
        from app.agents.orchestrator import _save_result

        contract = await make_contract(db, owner_id("a"))
        now = datetime.now(UTC)
        await _save_result(
            db, str(contract.id), AgentType.risk_clause, {"findings": []}, None, now, now
        )
        await db.delete(contract)
        await db.commit()

        remaining = (await db.execute(select(func.count(AnalysisResult.id)))).scalar()
        assert remaining == 0


class TestSkillStoreScoping:
    """Reads see own + global; writes land under the writer's own id (KNOWN_ISSUES #6)."""

    async def test_patterns_are_written_under_the_owner(self, db):
        from app.services.skill_store import save_new_patterns

        owner = owner_id("a")
        assert await save_new_patterns(db, [finding()], owner_id=owner) == 1

        row = (await db.execute(select(ClausePattern))).scalar_one()
        assert row.owner_id == owner

    async def test_another_owner_cannot_read_them(self, db):
        from app.services.skill_store import get_active_patterns, save_new_patterns

        await save_new_patterns(db, [finding()], owner_id=owner_id("a"))

        mine = await get_active_patterns(db, owner_id=owner_id("a"))
        theirs = await get_active_patterns(db, owner_id=owner_id("b"))
        assert len(mine) == 1
        assert theirs == []

    async def test_global_patterns_are_visible_to_everyone(self, db):
        from app.services.skill_store import GLOBAL_OWNER, fingerprint, get_active_patterns

        db.add(
            ClausePattern(
                owner_id=GLOBAL_OWNER,
                pattern_name="seeded:high",
                fingerprint=fingerprint("seeded", "contoh"),
                description="d",
                example_text="contoh",
                severity="high",
                times_matched=0,
                confidence=0.9,
                is_active=True,
            )
        )
        await db.commit()

        for tag in ("a", "b", "c"):
            patterns = await get_active_patterns(db, owner_id=owner_id(tag))
            assert [p["pattern_name"] for p in patterns] == ["seeded:high"]

    async def test_legacy_patterns_are_never_read(self, db):
        """Provenance is unknown, so they stay in the table but out of every prompt."""
        from app.services.skill_store import LEGACY_OWNER, fingerprint, get_active_patterns

        db.add(
            ClausePattern(
                owner_id=LEGACY_OWNER,
                pattern_name="old:high",
                fingerprint=fingerprint("old", "teks"),
                description="d",
                example_text="teks",
                severity="high",
                times_matched=5,
                confidence=0.99,
                is_active=True,
            )
        )
        await db.commit()

        assert await get_active_patterns(db, owner_id=owner_id("a")) == []
        assert await get_active_patterns(db, owner_id=LEGACY_OWNER) == []
        assert (await db.execute(select(func.count(ClausePattern.id)))).scalar() == 1

    async def test_two_owners_can_hold_the_same_clause(self, db):
        """The unique key is (owner_id, fingerprint); a global one would reject the second."""
        from app.services.skill_store import save_new_patterns

        assert await save_new_patterns(db, [finding()], owner_id=owner_id("a")) == 1
        assert await save_new_patterns(db, [finding()], owner_id=owner_id("b")) == 1
        assert (await db.execute(select(func.count(ClausePattern.id)))).scalar() == 2

    async def test_reobserving_bumps_the_counter_instead_of_inserting(self, db):
        from app.services.skill_store import save_new_patterns

        owner = owner_id("a")
        await save_new_patterns(db, [finding()], owner_id=owner)
        assert await save_new_patterns(db, [finding()], owner_id=owner) == 0

        row = (await db.execute(select(ClausePattern))).scalar_one()
        assert row.times_matched == 2

    async def test_a_private_reobservation_does_not_touch_the_global_row(self, db):
        """A runtime write to shared state would let one user reorder everyone's context."""
        from app.services.skill_store import (
            GLOBAL_OWNER,
            fingerprint,
            get_active_patterns,
            save_new_patterns,
        )

        text = "Dilarang bekerja 5 tahun."
        db.add(
            ClausePattern(
                owner_id=GLOBAL_OWNER,
                pattern_name="non_compete:critical",
                fingerprint=fingerprint("non_compete", text),
                description="seeded",
                example_text=text,
                severity="critical",
                times_matched=0,
                confidence=0.9,
                is_active=True,
            )
        )
        await db.commit()

        await save_new_patterns(db, [finding(text=text)], owner_id=owner_id("a"))

        global_row = (
            await db.execute(
                select(ClausePattern).where(ClausePattern.owner_id == GLOBAL_OWNER)
            )
        ).scalar_one()
        assert global_row.times_matched == 0

        # Both rows share a fingerprint, so the reader must show exactly one — the private
        # one, which carries the owner's own observation count.
        patterns = await get_active_patterns(db, owner_id=owner_id("a"))
        assert len(patterns) == 1
        assert patterns[0]["times_matched"] == 1

    async def test_low_confidence_findings_are_not_stored(self, db):
        from app.services.skill_store import CONFIDENCE_THRESHOLD, save_new_patterns

        low = finding(confidence=CONFIDENCE_THRESHOLD - 0.01)
        assert await save_new_patterns(db, [low], owner_id=owner_id("a")) == 0
        assert (await db.execute(select(func.count(ClausePattern.id)))).scalar() == 0

    async def test_limit_is_enforced(self, db):
        from app.services.skill_store import get_active_patterns, save_new_patterns

        findings = [finding(clause_type=f"type_{i}", text=f"teks {i}") for i in range(10)]
        await save_new_patterns(db, findings, owner_id=owner_id("a"))
        assert len(await get_active_patterns(db, limit=4, owner_id=owner_id("a"))) == 4

    async def test_inactive_patterns_are_excluded(self, db):
        from app.services.skill_store import get_active_patterns, save_new_patterns

        owner = owner_id("a")
        await save_new_patterns(db, [finding()], owner_id=owner)
        row = (await db.execute(select(ClausePattern))).scalar_one()
        row.is_active = False
        await db.commit()

        assert await get_active_patterns(db, owner_id=owner) == []

    async def test_ordering_is_most_matched_first(self, db):
        from app.services.skill_store import get_active_patterns, save_new_patterns

        owner = owner_id("a")
        rare = finding(clause_type="rare", text="jarang")
        common = finding(clause_type="common", text="sering")
        await save_new_patterns(db, [rare, common], owner_id=owner)
        for _ in range(3):
            await save_new_patterns(db, [common], owner_id=owner)

        names = [p["pattern_name"] for p in await get_active_patterns(db, owner_id=owner)]
        assert names[0].startswith("common")


class TestSeedLoader:
    async def test_seeds_the_global_bucket(self, db):
        from app.services.seed_loader import clause_pattern_count, seed_clause_patterns
        from app.services.skill_store import GLOBAL_OWNER

        added = await seed_clause_patterns(db)
        assert added > 0
        assert await clause_pattern_count(db) == added

        owners = set(
            (await db.execute(select(ClausePattern.owner_id))).scalars().all()
        )
        assert owners == {GLOBAL_OWNER}

    async def test_is_idempotent(self, db):
        from app.services.seed_loader import clause_pattern_count, seed_clause_patterns

        first = await seed_clause_patterns(db)
        assert await seed_clause_patterns(db) == 0
        assert await clause_pattern_count(db) == first

    async def test_seeded_rows_start_at_zero_matches(self, db):
        """So a single real observation outranks them in the RAG ordering."""
        from app.services.seed_loader import seed_clause_patterns

        await seed_clause_patterns(db)
        counts = set(
            (await db.execute(select(ClausePattern.times_matched))).scalars().all()
        )
        assert counts == {0}

    async def test_a_private_copy_does_not_block_seeding(self, db):
        """Seeding checks only the global bucket; otherwise a user could suppress a seed row."""
        from app.services.seed_loader import clause_patterns_from_dataset, seed_clause_patterns
        from app.services.skill_store import GLOBAL_OWNER

        first = clause_patterns_from_dataset()[0]
        db.add(ClausePattern(**{**first, "owner_id": owner_id("a"), "times_matched": 1}))
        await db.commit()

        added = await seed_clause_patterns(db)
        global_count = (
            await db.execute(
                select(func.count(ClausePattern.id)).where(
                    ClausePattern.owner_id == GLOBAL_OWNER
                )
            )
        ).scalar()
        assert added == global_count == len(clause_patterns_from_dataset())


class TestReaper:
    """One UPDATE ... RETURNING, so two workers sweeping concurrently cannot double-report."""

    async def _age(self, db, contract, seconds: int):
        from sqlalchemy import update

        stale = datetime.now(UTC) - timedelta(seconds=seconds)
        await db.execute(
            update(Contract).where(Contract.id == contract.id).values(updated_at=stale)
        )
        await db.commit()

    async def test_sweeps_a_stale_processing_contract(self, db):
        from app.config import get_settings
        from app.services.reaper import REAPED_MESSAGE, reap_stuck_contracts

        contract = await make_contract(db, owner_id("a"), ContractStatus.processing)
        await self._age(db, contract, get_settings().stuck_contract_timeout_seconds + 60)

        assert await reap_stuck_contracts() == 1

        await db.refresh(contract)
        assert contract.status is ContractStatus.failed
        assert contract.error == REAPED_MESSAGE

    async def test_leaves_a_fresh_contract_alone(self, db):
        from app.services.reaper import reap_stuck_contracts

        contract = await make_contract(db, owner_id("a"), ContractStatus.processing)
        assert await reap_stuck_contracts() == 0

        await db.refresh(contract)
        assert contract.status is ContractStatus.processing

    @pytest.mark.parametrize("status", [ContractStatus.done, ContractStatus.failed])
    async def test_never_touches_terminal_states(self, db, status):
        from app.config import get_settings
        from app.services.reaper import reap_stuck_contracts

        contract = await make_contract(db, owner_id("a"), status)
        await self._age(db, contract, get_settings().stuck_contract_timeout_seconds + 600)

        assert await reap_stuck_contracts() == 0
        await db.refresh(contract)
        assert contract.status is status

    async def test_sweeps_a_stale_uploaded_contract(self, db):
        """The enqueue never landed and the process died before it could mark it failed."""
        from app.config import get_settings
        from app.services.reaper import reap_stuck_contracts

        contract = await make_contract(db, owner_id("a"), ContractStatus.uploaded)
        await self._age(db, contract, get_settings().stuck_contract_timeout_seconds + 60)

        assert await reap_stuck_contracts() == 1
        await db.refresh(contract)
        assert contract.status is ContractStatus.failed

    async def test_a_second_sweep_reports_nothing(self, db):
        from app.config import get_settings
        from app.services.reaper import reap_stuck_contracts

        contract = await make_contract(db, owner_id("a"), ContractStatus.processing)
        await self._age(db, contract, get_settings().stuck_contract_timeout_seconds + 60)

        assert await reap_stuck_contracts() == 1
        assert await reap_stuck_contracts() == 0

    async def test_list_stuck_does_not_mutate(self, db):
        from app.config import get_settings
        from app.services.reaper import list_stuck_contracts

        contract = await make_contract(db, owner_id("a"), ContractStatus.processing)
        await self._age(db, contract, get_settings().stuck_contract_timeout_seconds + 60)

        assert str(contract.id) in await list_stuck_contracts()
        await db.refresh(contract)
        assert contract.status is ContractStatus.processing


class TestOwnerScopedLookup:
    """`load_owned_contract` is the only place the ownership rule lives."""

    def _request(self, owner: str):
        from types import SimpleNamespace

        return SimpleNamespace(state=SimpleNamespace(owner_id=owner))

    async def test_owner_can_load(self, db):
        from app.api.deps import load_owned_contract

        owner = owner_id("a")
        contract = await make_contract(db, owner)
        loaded = await load_owned_contract(str(contract.id), self._request(owner), db)
        assert loaded.id == contract.id

    async def test_stranger_gets_404(self, db):
        from fastapi import HTTPException

        from app.api.deps import load_owned_contract

        contract = await make_contract(db, owner_id("a"))
        with pytest.raises(HTTPException) as exc:
            await load_owned_contract(str(contract.id), self._request(owner_id("b")), db)
        assert exc.value.status_code == 404

    async def test_missing_contract_gets_the_same_404(self, db):
        """Identical to the foreign case, so a 404 does not confirm the UUID exists."""
        from fastapi import HTTPException

        from app.api.deps import NOT_FOUND_DETAIL, load_owned_contract

        with pytest.raises(HTTPException) as exc:
            await load_owned_contract(str(uuid.uuid4()), self._request(owner_id("a")), db)
        assert exc.value.status_code == 404
        assert exc.value.detail == NOT_FOUND_DETAIL

    async def test_malformed_uuid_is_404_not_500(self, db):
        """The id comes straight from the URL, so it is untrusted input."""
        from fastapi import HTTPException

        from app.api.deps import load_owned_contract

        with pytest.raises(HTTPException) as exc:
            await load_owned_contract("not-a-uuid", self._request(owner_id("a")), db)
        assert exc.value.status_code == 404


class TestPerLoopEngine:
    """
    asyncpg connections belong to the loop that created them, and RQ runs every job in a
    fresh `asyncio.run`. The unit suite proves engines differ per loop; this proves a second
    loop can actually *query*, which is the failure that reached production.
    """

    def test_two_sequential_loops_can_both_query(self, migrated_database):
        import asyncio

        from sqlalchemy import text

        from app.db.session import AsyncSessionLocal, dispose_engine

        async def job():
            try:
                async with AsyncSessionLocal() as session:
                    return (await session.execute(text("SELECT 1"))).scalar()
            finally:
                await dispose_engine()

        assert asyncio.run(job()) == 1
        assert asyncio.run(job()) == 1, "second job hit a connection from the first loop"

    def test_a_write_in_one_loop_is_visible_in_the_next(self, migrated_database):
        import asyncio

        from sqlalchemy import text

        from app.db.session import AsyncSessionLocal, dispose_engine

        contract_id = uuid.uuid4()

        async def write():
            try:
                async with AsyncSessionLocal() as session:
                    session.add(
                        Contract(
                            id=contract_id,
                            owner_id=owner_id("a"),
                            filename="x.txt",
                            raw_text="teks",
                            status=ContractStatus.uploaded,
                        )
                    )
                    await session.commit()
            finally:
                await dispose_engine()

        async def read():
            try:
                async with AsyncSessionLocal() as session:
                    return (
                        await session.execute(
                            text("SELECT count(*) FROM contracts WHERE id = :cid"),
                            {"cid": str(contract_id)},
                        )
                    ).scalar()
            finally:
                await dispose_engine()

        asyncio.run(write())
        assert asyncio.run(read()) == 1
