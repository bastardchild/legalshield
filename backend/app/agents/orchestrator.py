import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agent_counter_draft import run_counter_draft_agent
from app.agents.agent_risk_clause import AgentRun, run_risk_clause_agent
from app.agents.agent_tax_compliance import run_tax_compliance_agent
from app.config import get_settings
from app.db.models import AgentType, AnalysisResult, Contract, ContractStatus, WhatsappSend
from app.db.session import AsyncSessionLocal
from app.services.skill_store import save_new_patterns

logger = logging.getLogger(__name__)

EMPTY_FINDINGS: dict = {"findings": []}


async def _save_result(
    db: AsyncSession,
    contract_id: str,
    agent_type: AgentType,
    result_json: dict | None,
    error: str | None,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> None:
    """
    Upsert an AnalysisResult row.

    A real ON CONFLICT against uq_analysis_results_contract_agent, so a re-run updates in
    place instead of racing a SELECT-then-INSERT.
    """
    stmt = pg_insert(AnalysisResult).values(
        contract_id=contract_id,
        agent_type=agent_type,
        result_json=result_json,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_analysis_results_contract_agent",
            set_={
                "result_json": stmt.excluded.result_json,
                "error": stmt.excluded.error,
                "started_at": stmt.excluded.started_at,
                "finished_at": stmt.excluded.finished_at,
            },
        )
    )
    await db.commit()


async def _set_status(contract_id: str, status: ContractStatus) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Contract)
            .where(Contract.id == contract_id)
            .values(status=status, updated_at=datetime.now(UTC))
        )
        await db.commit()


async def _persist_agent(
    contract_id: str,
    agent_type: AgentType,
    outcome: AgentRun | BaseException,
    label: str,
    owner_id: str | None = None,
) -> dict | None:
    """
    Store one agent's outcome. Returns its payload, or None if it failed.

    Failures are recorded on the row rather than raised, so a single agent going down
    does not discard the work of the others.
    """
    async with AsyncSessionLocal() as db:
        if isinstance(outcome, BaseException):
            logger.error(f"[{label}] Failed: {outcome!r}")
            await _save_result(db, contract_id, agent_type, None, str(outcome), None, None)
            return None

        await _save_result(
            db,
            contract_id,
            agent_type,
            outcome.result,
            None,
            outcome.started_at,
            outcome.finished_at,
        )
        if agent_type is AgentType.risk_clause:
            try:
                added = await save_new_patterns(
                    db, outcome.result.get("findings", []), owner_id=owner_id
                )
                if added:
                    logger.info(f"[SkillStore] Stored {added} new pattern(s).")
            except Exception as e:
                # The skill store is an optimisation; never fail an analysis over it.
                logger.warning(f"[SkillStore] Could not save patterns: {e}")
        return outcome.result


async def run_analysis(contract_id: str) -> None:
    """
    Orchestrator entrypoint, invoked by the RQ worker.

    Agents A and B run concurrently; C runs afterwards because it consumes their output.
    The contract ends as `done` if anything useful was produced, `failed` only if every
    agent failed.
    """
    logger.info(f"[Orchestrator] Starting analysis for contract {contract_id}")

    try:
        await _set_status(contract_id, ContractStatus.processing)

        async with AsyncSessionLocal() as db:
            contract = (
                await db.execute(select(Contract).where(Contract.id == contract_id))
            ).scalar_one_or_none()
            if contract is None:
                logger.error(f"[Orchestrator] Contract {contract_id} not found; abandoning job.")
                return
            raw_text = contract.raw_text or ""
            # The skill store is scoped to this owner: patterns are read from their own set
            # plus the curated global one, and written only under their id.
            owner_id = contract.owner_id

        if not raw_text.strip():
            logger.error(f"[Orchestrator] Contract {contract_id} has no text to analyse.")
            await _set_status(contract_id, ContractStatus.failed)
            return

        # Preload RAG + legal refs before the parallel window so A/B don't
        # each hit the DB/seed cache inside the gather — saves ~100-300ms.
        from app.agents.agent_risk_clause import _known_patterns_text as _risk_rag
        from app.agents.agent_tax_compliance import _legal_references as _tax_refs

        try:
            preloaded_rag = await _risk_rag(owner_id)
        except Exception as e:
            logger.warning(f"[Orchestrator] Could not preload RAG: {e}")
            preloaded_rag = None
        try:
            preloaded_refs = _tax_refs()
        except Exception as e:
            logger.warning(f"[Orchestrator] Could not preload legal refs: {e}")
            preloaded_refs = None

        # Phase 1 — A and B in parallel.
        t0 = datetime.now(UTC)
        logger.info("[Orchestrator] Agents A & B starting in parallel")
        risk_coro = run_risk_clause_agent(raw_text, owner_id=owner_id, known_patterns=preloaded_rag) if preloaded_rag is not None else run_risk_clause_agent(raw_text, owner_id=owner_id)
        tax_coro = run_tax_compliance_agent(raw_text, legal_refs=preloaded_refs) if preloaded_refs is not None else run_tax_compliance_agent(raw_text)
        risk_outcome, tax_outcome = await asyncio.gather(
            risk_coro,
            tax_coro,
            return_exceptions=True,
        )
        elapsed = (datetime.now(UTC) - t0).total_seconds()
        logger.info(f"[Orchestrator] Agents A & B finished in {elapsed:.2f}s (parallel)")

        risk_result = await _persist_agent(
            contract_id, AgentType.risk_clause, risk_outcome, "RiskClauseAgent",
            owner_id=owner_id,
        )
        tax_result = await _persist_agent(
            contract_id, AgentType.tax_compliance, tax_outcome, "TaxComplianceAgent"
        )

        # Phase 2 — C consumes A and B. Runs even if one upstream agent failed, using
        # empty findings in its place; a counter-draft from partial input still has value.
        logger.info("[Orchestrator] Starting agent C (counter-draft)")
        try:
            counter_outcome: AgentRun | BaseException = await run_counter_draft_agent(
                raw_text,
                risk_result or EMPTY_FINDINGS,
                tax_result or EMPTY_FINDINGS,
            )
        except Exception as e:
            counter_outcome = e
        counter_result = await _persist_agent(
            contract_id, AgentType.counter_draft, counter_outcome, "CounterDraftAgent"
        )

        produced_anything = any(r is not None for r in (risk_result, tax_result, counter_result))
        final = ContractStatus.done if produced_anything else ContractStatus.failed
        await _set_status(contract_id, final)

        # Automatic WhatsApp link notification (only on done, only if opted in).
        if final == ContractStatus.done:
            try:
                await _maybe_send_whatsapp(contract_id)
            except Exception:
                logger.exception(f"[Whatsapp] notify failed for {contract_id} (non-fatal)")

        logger.info(f"[Orchestrator] Analysis finished for {contract_id} with status={final.value}")

    except Exception as e:
        logger.exception(f"[Orchestrator] Fatal error for {contract_id}: {e}")
        try:
            await _set_status(contract_id, ContractStatus.failed)
        except Exception:
            # Nothing more we can do; the reaper will pick this contract up.
            logger.exception(f"[Orchestrator] Could not mark {contract_id} as failed.")


async def _maybe_send_whatsapp(contract_id: str) -> None:
    """
    Automatic link notification via Fonnte. Only sends if the contract opted in
    and has a valid phone. Idempotent: skips if a 'sent' row already exists.
    Never raises — failures are logged and stored as a 'failed' row.
    """
    async with AsyncSessionLocal() as db:
        contract = (
            await db.execute(select(Contract).where(Contract.id == contract_id))
        ).scalar_one_or_none()
        if contract is None or not contract.notify_whatsapp or not contract.whatsapp_phone:
            return

        # Idempotency: don't send twice for the same contract
        existing = (
            await db.execute(
                select(WhatsappSend).where(
                    WhatsappSend.contract_id == contract.id,
                    WhatsappSend.status == "sent",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(f"[Whatsapp] Already sent for {contract_id}, skipping.")
            return

        phone = contract.whatsapp_phone
        filename = contract.filename or ""
        base_url = get_settings().app_base_url.rstrip("/")
        result_url = f"{base_url}/contracts/{contract_id}"

    # Send outside the DB session (network I/O)
    from app.services.whatsapp import send_link_notification

    outcome = await send_link_notification(contract_id, phone, result_url, filename)

    # Persist audit row in a fresh session
    async with AsyncSessionLocal() as db:
        row = WhatsappSend(
            contract_id=contract_id,
            recipient_phone=phone,
            result_url=result_url,
            status=outcome["status"],
            provider_message_id=outcome.get("provider_message_id"),
            provider_request_id=outcome.get("provider_request_id"),
            error=outcome.get("error"),
        )
        db.add(row)
        await db.commit()
        logger.info(f"[Whatsapp] Audit row for {contract_id}: status={outcome['status']}")
