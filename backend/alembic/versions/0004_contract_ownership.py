"""contract ownership

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-23

`contracts.owner_id` is the authorization key. Reads were previously scoped by nothing at
all, so anyone holding (or guessing) a contract UUID could read someone else's analysis.
Every contract is now stamped with the signed anonymous owner id of the uploader and every
read is filtered by it.

Existing rows predate ownership, so they are backfilled with the `legacy` sentinel. That
value can never be produced by `security.new_owner_id()` (which always returns 32 hex
chars), so legacy contracts stay in the database for auditing but are unreachable through
the UI. Deleting them instead would destroy user data to satisfy a schema change.
"""
import sqlalchemy as sa

from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

LEGACY_OWNER = 'legacy'


def upgrade() -> None:
    op.add_column('contracts', sa.Column('owner_id', sa.String(64), nullable=True))
    op.execute(f"UPDATE contracts SET owner_id = '{LEGACY_OWNER}' WHERE owner_id IS NULL")
    op.alter_column('contracts', 'owner_id', nullable=False)
    # Every scoped read is (id, owner_id) or (owner_id, created_at) for the list view.
    op.create_index('ix_contracts_owner_id', 'contracts', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_contracts_owner_id', table_name='contracts')
    op.drop_column('contracts', 'owner_id')
