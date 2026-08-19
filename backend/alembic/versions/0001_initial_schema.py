"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE IF NOT EXISTS contract_status AS ENUM ('uploaded', 'processing', 'done', 'failed')")
    op.execute("CREATE TYPE IF NOT EXISTS agent_type AS ENUM ('risk_clause', 'tax_compliance', 'counter_draft')")

    op.create_table(
        'contracts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('filename', sa.String(512), nullable=False),
        sa.Column('raw_text', sa.Text, nullable=True),
        sa.Column('status', sa.Enum('uploaded', 'processing', 'done', 'failed', name='contract_status'), nullable=False, server_default='uploaded'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'analysis_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('contract_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('contracts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_type', sa.Enum('risk_clause', 'tax_compliance', 'counter_draft', name='agent_type'), nullable=False),
        sa.Column('result_json', postgresql.JSONB, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'clause_patterns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('pattern_name', sa.String(256), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('example_text', sa.Text, nullable=False),
        sa.Column('severity', sa.String(32), nullable=False, server_default='medium'),
        sa.Column('times_matched', sa.Integer, nullable=False, server_default='1'),
        sa.Column('confidence', sa.Float, nullable=False, server_default='1.0'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'negotiation_sends',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('contract_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('contracts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recipient_email', sa.String(256), nullable=True),
        sa.Column('counter_draft_text', sa.Text, nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('status', sa.String(32), nullable=False, server_default='stub'),
    )

    op.create_index('ix_analysis_results_contract_id', 'analysis_results', ['contract_id'])
    op.create_index('ix_contracts_status', 'contracts', ['status'])


def downgrade() -> None:
    op.drop_table('negotiation_sends')
    op.drop_table('clause_patterns')
    op.drop_table('analysis_results')
    op.drop_table('contracts')
    op.execute("DROP TYPE IF EXISTS agent_type")
    op.execute("DROP TYPE IF EXISTS contract_status")
