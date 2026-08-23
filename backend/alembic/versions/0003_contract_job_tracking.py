"""contract job tracking

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23

`contracts.job_id` lets the reaper distinguish a job that is still running from one whose
worker died, and `contracts.error` surfaces the reason a contract failed (a dead Redis at
upload time, or a sweep by the reaper) instead of leaving the UI with a bare status.
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contracts', sa.Column('job_id', sa.String(64), nullable=True))
    op.add_column('contracts', sa.Column('error', sa.Text(), nullable=True))
    # The reaper scans by (status, updated_at); the status-only index is not selective
    # enough once most rows are `done`.
    op.create_index('ix_contracts_status_updated_at', 'contracts', ['status', 'updated_at'])


def downgrade() -> None:
    op.drop_index('ix_contracts_status_updated_at', table_name='contracts')
    op.drop_column('contracts', 'error')
    op.drop_column('contracts', 'job_id')
