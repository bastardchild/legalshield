"""
Reaper for contracts that never reached a terminal state (KNOWN_ISSUES #9).

A contract can be stranded two ways:

* `uploaded` — the row was committed but the enqueue never landed. The upload route now
  marks these `failed` inline, but a process killed between the two steps still leaves one.
* `processing` — the orchestrator claimed the contract and then its worker died (OOM kill,
  container restart, flushed Redis). Nothing else will ever move that row.

Either way the UI polls forever, because the front-end only stops on `done`/`failed`.
The reaper is deliberately conservative: it only touches rows whose `updated_at` is older
than `stuck_contract_timeout_seconds`, which is set above RQ's `job_timeout`, so a slow
but healthy job is never reaped.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app.config import get_settings
from app.db.models import Contract, ContractStatus
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

STUCK_STATUSES = (ContractStatus.uploaded, ContractStatus.processing)
REAPED_MESSAGE = (
    "Analisis dihentikan otomatis: proses tidak selesai dalam batas waktu. "
    "Silakan unggah ulang kontrak."
)


async def reap_stuck_contracts() -> int:
    """
    Mark timed-out contracts as failed. Returns the number of rows changed.

    Runs as a single UPDATE so two workers sweeping concurrently cannot double-report.
    """
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_contract_timeout_seconds)

    async with AsyncSessionLocal() as db:
        stmt = (
            update(Contract)
            .where(
                Contract.status.in_(STUCK_STATUSES),
                # updated_at is nullable on legacy rows; fall back to created_at.
                or_(
                    Contract.updated_at < cutoff,
                    Contract.updated_at.is_(None) & (Contract.created_at < cutoff),
                ),
            )
            .values(
                status=ContractStatus.failed,
                error=REAPED_MESSAGE,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(Contract.id)
        )
        result = await db.execute(stmt)
        reaped = result.scalars().all()
        await db.commit()

    if reaped:
        logger.warning(
            f"[Reaper] Marked {len(reaped)} stuck contract(s) as failed: "
            + ", ".join(str(cid) for cid in reaped[:10])
        )
    return len(reaped)


async def list_stuck_contracts() -> list[str]:
    """Ids the reaper would sweep right now. Diagnostics only; does not mutate."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.stuck_contract_timeout_seconds)
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(Contract.id).where(
                Contract.status.in_(STUCK_STATUSES),
                Contract.updated_at < cutoff,
            )
        )
        return [str(cid) for cid in rows.scalars().all()]


def run_reaper_forever() -> None:
    """
    Blocking sweep loop, run in a background thread by the RQ worker.

    Lives with the worker rather than the API so it scales with job capacity and does not
    depend on a web request arriving.
    """
    interval = get_settings().reaper_interval_seconds
    if interval <= 0:
        logger.info("[Reaper] Disabled (reaper_interval_seconds=0).")
        return

    logger.info(f"[Reaper] Starting sweep loop every {interval}s.")
    while True:
        try:
            asyncio.run(_sweep_once())
        except Exception as e:
            # Never let a transient DB error kill the loop.
            logger.warning(f"[Reaper] Sweep failed: {e}")
        time.sleep(interval)


async def _sweep_once() -> None:
    """One sweep plus engine teardown, since each iteration runs in its own event loop."""
    from app.db.session import dispose_engine

    try:
        await reap_stuck_contracts()
    finally:
        await dispose_engine()
