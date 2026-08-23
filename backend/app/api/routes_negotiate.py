import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import load_owned_contract
from app.db.models import AgentType, AnalysisResult, Contract, NegotiationSend
from app.db.session import get_db
from app.schemas import NegotiationSendRequest, NegotiationSendResponse
from app.services.mailer import send_counter_draft

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/contracts/{contract_id}/send", response_model=NegotiationSendResponse)
async def send_negotiation(
    body: NegotiationSendRequest,
    contract: Contract = Depends(load_owned_contract),
    db: AsyncSession = Depends(get_db),
):
    if contract.status.value != "done":
        raise HTTPException(status_code=409, detail="Analysis not complete yet.")

    ar_result = await db.execute(
        select(AnalysisResult).where(
            AnalysisResult.contract_id == contract.id,
            AnalysisResult.agent_type == AgentType.counter_draft,
        )
    )
    ar = ar_result.scalar_one_or_none()
    counter_draft_text = ""
    if ar and ar.result_json:
        counter_draft_text = ar.result_json.get("counter_draft", "")

    outcome = send_counter_draft(str(contract.id), body.recipient_email, counter_draft_text)

    send_record = NegotiationSend(
        contract_id=contract.id,
        recipient_email=body.recipient_email,
        counter_draft_text=counter_draft_text,
        status=outcome["status"],
    )
    db.add(send_record)
    await db.commit()
    await db.refresh(send_record)

    # A delivery failure is reported as 502 *after* the attempt is recorded, so the row is
    # an audit trail of what was tried rather than only of what succeeded.
    if outcome["status"] == "failed":
        raise HTTPException(
            status_code=502,
            detail=outcome.get("error") or "Draft tidak dapat dikirim. Periksa konfigurasi SMTP.",
        )

    return NegotiationSendResponse(
        id=str(send_record.id),
        contract_id=str(send_record.contract_id),
        recipient_email=send_record.recipient_email,
        status=send_record.status,
        sent_at=send_record.sent_at,
    )
