"""whatsapp link notifications

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29

Adds opt-in WhatsApp link notification for completed analyses:

* `contracts.whatsapp_phone` (nullable, normalized E.164 digits)
* `contracts.notify_whatsapp` (boolean, default false)
* `whatsapp_sends` audit table (link-only, no draft text)

Fonnte API: POST {FONNTE_BASE_URL}/send  (https://docs.fonnte.com/api-send-message/)
The link sent is {APP_BASE_URL}/contracts/{id} — localhost by default, overridden via .env.
"""

import sqlalchemy as sa

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contracts', sa.Column('whatsapp_phone', sa.String(32), nullable=True))
    op.add_column(
        'contracts',
        sa.Column('notify_whatsapp', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_contracts_whatsapp_phone', 'contracts', ['whatsapp_phone'])

    op.create_table(
        'whatsapp_sends',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('contract_id', sa.UUID(), nullable=False),
        sa.Column('recipient_phone', sa.String(32), nullable=False),
        sa.Column('result_url', sa.Text(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('provider_message_id', sa.String(128), nullable=True),
        sa.Column('provider_request_id', sa.String(64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_whatsapp_sends_contract_id', 'whatsapp_sends', ['contract_id'])


def downgrade() -> None:
    op.drop_index('ix_whatsapp_sends_contract_id', table_name='whatsapp_sends')
    op.drop_table('whatsapp_sends')
    op.drop_index('ix_contracts_whatsapp_phone', table_name='contracts')
    op.drop_column('contracts', 'notify_whatsapp')
    op.drop_column('contracts', 'whatsapp_phone')
