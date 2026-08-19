import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Contract, AnalysisResult, NegotiationSend, AgentType
from app.services.mailer import send_counter_draft
from app.schemas import NegotiationSendRequest, NegotiationSendResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/contracts/{contract_id}/send", response_model=NegotiationSendResponse)
async def send_negotiation(
    contract_id: str,
    body: NegotiationSendRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if contract.status.value != "done":
        raise HTTPException(status_code=409, detail="Analysis not complete yet.")

    # Get counter-draft text
    ar_result = await db.execute(
        select(AnalysisResult).where(
            AnalysisResult.contract_id == contract_id,
            AnalysisResult.agent_type == AgentType.counter_draft,
        )
    )
    ar = ar_result.scalar_one_or_none()
    counter_draft_text = ""
    if ar and ar.result_json:
        counter_draft_text = ar.result_json.get("counter_draft", "")

    # Stub send
    send_counter_draft(contract_id, body.recipient_email, counter_draft_text)

    send_record = NegotiationSend(
        contract_id=contract_id,
        recipient_email=body.recipient_email,
        counter_draft_text=counter_draft_text,
        status="stub",
    )
    db.add(send_record)
    await db.commit()
    await db.refresh(send_record)

    return NegotiationSendResponse(
        id=str(send_record.id),
        contract_id=str(send_record.contract_id),
        recipient_email=send_record.recipient_email,
        status=send_record.status,
        sent_at=send_record.sent_at,
    )
