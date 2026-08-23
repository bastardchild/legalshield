import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import load_owned_contract
from app.db.models import AnalysisResult, Contract
from app.db.session import get_db
from app.schemas import AnalysisResultOut, ContractResultResponse, ContractStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/contracts/{contract_id}/status", response_model=ContractStatusResponse)
async def get_contract_status(contract: Contract = Depends(load_owned_contract)):
    return ContractStatusResponse(
        id=str(contract.id),
        filename=contract.filename,
        status=contract.status.value,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


@router.get("/contracts/{contract_id}/result", response_model=ContractResultResponse)
async def get_contract_result(
    contract: Contract = Depends(load_owned_contract),
    db: AsyncSession = Depends(get_db),
):
    ar_result = await db.execute(
        select(AnalysisResult).where(AnalysisResult.contract_id == contract.id)
    )
    analysis_rows = ar_result.scalars().all()

    return ContractResultResponse(
        id=str(contract.id),
        filename=contract.filename,
        status=contract.status.value,
        analysis_results=[
            AnalysisResultOut(
                agent_type=r.agent_type.value,
                result_json=r.result_json,
                error=r.error,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in analysis_rows
        ],
    )
