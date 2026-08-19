import uuid
from typing import Optional, List
from pydantic import BaseModel, EmailStr
from datetime import datetime


class ContractUploadResponse(BaseModel):
    id: str
    filename: str
    status: str


class AnalysisResultOut(BaseModel):
    agent_type: str
    result_json: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


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
    analysis_results: List[AnalysisResultOut] = []


class NegotiationSendRequest(BaseModel):
    recipient_email: Optional[str] = None


class NegotiationSendResponse(BaseModel):
    id: str
    contract_id: str
    recipient_email: Optional[str]
    status: str
    sent_at: datetime
