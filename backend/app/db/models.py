import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base


class ContractStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    done = "done"
    failed = "failed"


class AgentType(str, enum.Enum):
    risk_clause = "risk_clause"
    tax_compliance = "tax_compliance"
    counter_draft = "counter_draft"


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_status", "status"),
        Index("ix_contracts_status_updated_at", "status", "updated_at"),
        Index("ix_contracts_owner_id", "owner_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Signed anonymous owner id from the cookie. Every read is scoped to it, so a leaked
    # contract UUID is no longer sufficient to read the analysis.
    owner_id = Column(String(64), nullable=False)
    filename = Column(String(512), nullable=False)
    raw_text = Column(Text, nullable=True)
    status = Column(
        Enum(ContractStatus, name="contract_status"),
        nullable=False,
        default=ContractStatus.uploaded,
    )
    # RQ job id, so the reaper can tell a genuinely lost job from a slow one.
    job_id = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)
    # WhatsApp notification (opt-in at upload). Phone is normalized E.164 digits.
    whatsapp_phone = Column(String(32), nullable=True)
    notify_whatsapp = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    analysis_results = relationship("AnalysisResult", back_populates="contract", cascade="all, delete-orphan")
    negotiation_sends = relationship("NegotiationSend", back_populates="contract", cascade="all, delete-orphan")
    whatsapp_sends = relationship("WhatsappSend", back_populates="contract", cascade="all, delete-orphan")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    # The orchestrator upserts on (contract_id, agent_type); without this constraint a
    # re-run can create a second row and every later upsert raises MultipleResultsFound.
    __table_args__ = (
        UniqueConstraint("contract_id", "agent_type", name="uq_analysis_results_contract_agent"),
        Index("ix_analysis_results_contract_id", "contract_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    agent_type = Column(
        Enum(AgentType, name="agent_type"),
        nullable=False,
    )
    result_json = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    contract = relationship("Contract", back_populates="analysis_results")


class ClausePattern(Base):
    __tablename__ = "clause_patterns"
    # Scoped per owner: a pattern learned from one user's contract must not enter another
    # user's RAG context (KNOWN_ISSUES #6). Two owners can legitimately observe the same
    # clause, so uniqueness is per (owner, fingerprint) rather than per fingerprint.
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "fingerprint", name="uq_clause_patterns_owner_fingerprint"
        ),
        Index("ix_clause_patterns_owner_id", "owner_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Owner who observed the pattern, or the GLOBAL_OWNER sentinel for curated seed data.
    owner_id = Column(String(64), nullable=False)
    pattern_name = Column(String(256), nullable=False)
    # Content hash of (clause_type, normalised example_text). Dedupes on substance rather
    # than on clause_type:severity, which collapsed unrelated clauses into one row.
    fingerprint = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    example_text = Column(Text, nullable=False)
    severity = Column(String(32), nullable=False, default="medium")  # low | medium | high | critical
    times_matched = Column(Integer, nullable=False, default=1)
    confidence = Column(Float, nullable=False, default=1.0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NegotiationSend(Base):
    __tablename__ = "negotiation_sends"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    recipient_email = Column(String(256), nullable=True)
    counter_draft_text = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(32), nullable=False, default="stub")  # stub | sent | failed

    contract = relationship("Contract", back_populates="negotiation_sends")


class WhatsappSend(Base):
    __tablename__ = "whatsapp_sends"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    recipient_phone = Column(String(32), nullable=False)
    result_url = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="stub")  # stub | sent | failed
    provider_message_id = Column(String(128), nullable=True)
    provider_request_id = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())

    contract = relationship("Contract", back_populates="whatsapp_sends")
