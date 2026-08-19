import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.db.models import Contract, AnalysisResult, ContractStatus, AgentType
from app.agents.agent_risk_clause import run_risk_clause_agent
from app.agents.agent_tax_compliance import run_tax_compliance_agent
from app.agents.agent_counter_draft import run_counter_draft_agent
from app.services.skill_store import save_new_patterns

logger = logging.getLogger(__name__)


async def _save_result(
    db: AsyncSession,
    contract_id: str,
    agent_type: AgentType,
    result_json: dict | None,
    error: str | None,
    started_at: datetime | None,
    finished_at: datetime | None,
):
    """Upsert an AnalysisResult row."""
    stmt = select(AnalysisResult).where(
        AnalysisResult.contract_id == contract_id,
        AnalysisResult.agent_type == agent_type,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        existing.result_json = result_json
        existing.error = error
        existing.started_at = started_at
        existing.finished_at = finished_at
    else:
        db.add(AnalysisResult(
            contract_id=contract_id,
            agent_type=agent_type,
            result_json=result_json,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
        ))
    await db.commit()


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


async def run_analysis(contract_id: str):
    """
    Main orchestrator — called by the RQ worker.
    Runs sub-agents A & B in parallel, then C after both finish.
    """
    logger.info(f"[Orchestrator] Starting analysis for contract {contract_id}")

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Contract)
            .where(Contract.id == contract_id)
            .values(status=ContractStatus.processing, updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
        result = await db.execute(select(Contract).where(Contract.id == contract_id))
        contract = result.scalar_one_or_none()
        if not contract:
            logger.error(f"Contract {contract_id} not found")
            return
        raw_text = contract.raw_text or ""

    try:
        # Phase 1: run A & B in parallel
        logger.info("[Orchestrator] Agents A & B starting in parallel")
        t0 = datetime.now(timezone.utc)
        risk_task = asyncio.create_task(run_risk_clause_agent(raw_text))
        tax_task = asyncio.create_task(run_tax_compliance_agent(raw_text))
        risk_result, tax_result = await asyncio.gather(risk_task, tax_task, return_exceptions=True)
        parallel_secs = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.info(f"[Orchestrator] Agents A & B finished in {parallel_secs:.2f}s (parallel)")

        # Persist A
        async with AsyncSessionLocal() as db:
            if isinstance(risk_result, Exception):
                logger.error(f"[RiskClauseAgent] Failed: {risk_result}")
                await _save_result(db, contract_id, AgentType.risk_clause, None, str(risk_result), None, None)
                risk_result = {"findings": []}
            else:
                await _save_result(
                    db, contract_id, AgentType.risk_clause, risk_result, None,
                    _dt(risk_result.get("_started_at", t0.isoformat())),
                    _dt(risk_result.get("_finished_at", datetime.now(timezone.utc).isoformat())),
                )
                await save_new_patterns(db, risk_result.get("findings", []))

        # Persist B
        async with AsyncSessionLocal() as db:
            if isinstance(tax_result, Exception):
                logger.error(f"[TaxComplianceAgent] Failed: {tax_result}")
                await _save_result(db, contract_id, AgentType.tax_compliance, None, str(tax_result), None, None)
                tax_result = {"findings": []}
            else:
                await _save_result(
                    db, contract_id, AgentType.tax_compliance, tax_result, None,
                    _dt(tax_result.get("_started_at", t0.isoformat())),
                    _dt(tax_result.get("_finished_at", datetime.now(timezone.utc).isoformat())),
                )

        # Phase 2: agent C (depends on A & B)
        logger.info("[Orchestrator] Starting agent C (counter-draft)")
        counter_result = await run_counter_draft_agent(raw_text, risk_result, tax_result)
        async with AsyncSessionLocal() as db:
            await _save_result(
                db, contract_id, AgentType.counter_draft, counter_result, None,
                _dt(counter_result.get("_started_at", datetime.now(timezone.utc).isoformat())),
                _dt(counter_result.get("_finished_at", datetime.now(timezone.utc).isoformat())),
            )

        # Mark done
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Contract).where(Contract.id == contract_id)
                .values(status=ContractStatus.done, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        logger.info(f"[Orchestrator] Analysis complete for {contract_id}")

    except Exception as e:
        logger.exception(f"[Orchestrator] Fatal error for {contract_id}: {e}")
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Contract).where(Contract.id == contract_id)
                .values(status=ContractStatus.failed, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
