from datetime import datetime

from pydantic import BaseModel


class ContractUploadResponse(BaseModel):
    id: str
    filename: str
    status: str


class AnalysisResultOut(BaseModel):
    agent_type: str
    result_json: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ContractStatusResponse(BaseModel):
    id: str
    filename: str
    status: str
    created_at: datetime
    updated_at: datetime


class ContractResultResponse(BaseModel):
    id: str
    filename: str
    status: str
    analysis_results: list[AnalysisResultOut] = []


class NegotiationSendRequest(BaseModel):
    recipient_email: str | None = None


class NegotiationSendResponse(BaseModel):
    id: str
    contract_id: str
    recipient_email: str | None
    status: str
    sent_at: datetime
