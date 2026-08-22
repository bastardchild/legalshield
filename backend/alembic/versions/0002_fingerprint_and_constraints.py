"""content fingerprints and uniqueness constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23

Three changes, all of which need existing rows cleaned up first:

1. `clause_patterns.fingerprint` — the skill store now dedupes on a content hash instead
   of on `clause_type:severity`, which collapsed unrelated clauses into a single row.
2. `uq_analysis_results_contract_agent` — backs the orchestrator's ON CONFLICT upsert.
3. `uq_clause_patterns_fingerprint` — makes the dedupe authoritative at the DB level.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

# Mirrors services.skill_store.fingerprint():
#   sha256("<clause_type>|<lower(trim(collapse_ws(example_text)))>")[:32]
# clause_type was previously encoded as the first segment of pattern_name.
BACKFILL_FINGERPRINT = r"""
UPDATE clause_patterns
SET fingerprint = substr(
    encode(
        sha256(
            convert_to(
                split_part(pattern_name, ':', 1)
                || '|'
                || lower(btrim(regexp_replace(coalesce(example_text, ''), '\s+', ' ', 'g'))),
                'UTF8'
            )
        ),
        'hex'
    ),
    1, 32
)
WHERE fingerprint IS NULL
"""

# Keep the most recently written row per (contract_id, agent_type). finished_at is the
# best signal of recency; created_at breaks ties for rows that never finished.
DEDUPE_ANALYSIS_RESULTS = """
DELETE FROM analysis_results a
USING analysis_results b
WHERE a.contract_id = b.contract_id
  AND a.agent_type = b.agent_type
  AND a.id <> b.id
  AND (
      coalesce(a.finished_at, a.created_at, 'epoch'::timestamptz),
      a.id::text
  ) < (
      coalesce(b.finished_at, b.created_at, 'epoch'::timestamptz),
      b.id::text
  )
"""

# Keep the best-established row per fingerprint (most matches, then highest confidence).
DEDUPE_CLAUSE_PATTERNS = """
DELETE FROM clause_patterns a
USING clause_patterns b
WHERE a.fingerprint = b.fingerprint
  AND a.id <> b.id
  AND (a.times_matched, a.confidence, a.id::text)
    < (b.times_matched, b.confidence, b.id::text)
"""


def upgrade() -> None:
    op.add_column('clause_patterns', sa.Column('fingerprint', sa.String(64), nullable=True))
    op.execute(BACKFILL_FINGERPRINT)
    op.alter_column('clause_patterns', 'fingerprint', nullable=False)

    op.execute(DEDUPE_ANALYSIS_RESULTS)
    op.create_unique_constraint(
        'uq_analysis_results_contract_agent',
        'analysis_results',
        ['contract_id', 'agent_type'],
    )

    op.execute(DEDUPE_CLAUSE_PATTERNS)
    op.create_unique_constraint(
        'uq_clause_patterns_fingerprint',
        'clause_patterns',
        ['fingerprint'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_clause_patterns_fingerprint', 'clause_patterns', type_='unique')
    op.drop_constraint('uq_analysis_results_contract_agent', 'analysis_results', type_='unique')
    op.drop_column('clause_patterns', 'fingerprint')
