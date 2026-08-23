"""per-tenant clause patterns

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23

`clause_patterns` was global. Prompt injection is mitigated by fencing but not eliminated
(KNOWN_ISSUES #6), and the skill store is the persistence mechanism that turned a one-off
injection into a lasting one: a crafted contract that produced a high-confidence "finding"
was stored and then injected as RAG context into *every other user's* later analysis.

Patterns are now scoped by `owner_id`, with two reserved sentinels that
`security.new_owner_id()` can never produce (it always returns 32 hex characters):

* `global` — the curated seed data from `seed/dataset1.json`. Read by everyone, written by
  nobody at runtime, so it cannot be poisoned.
* `legacy` — patterns learned before ownership existed. Retained for auditing but never
  read, because there is no way to tell which of them came from a hostile upload.

The uniqueness constraint moves from `(fingerprint)` to `(owner_id, fingerprint)`: two
owners observing the same clause must each get their own row, or the second one's `INSERT`
would collide with a row it cannot see.
"""
import sqlalchemy as sa

from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

GLOBAL_OWNER = 'global'
LEGACY_OWNER = 'legacy'

# times_matched is 0 only for seeded rows (seed_loader sets SEED_TIMES_MATCHED = 0 so real
# observations outrank them); anything that has matched at least once was learned at
# runtime and is therefore of unverifiable provenance.
CLASSIFY_EXISTING = f"""
UPDATE clause_patterns
SET owner_id = CASE WHEN times_matched = 0 THEN '{GLOBAL_OWNER}' ELSE '{LEGACY_OWNER}' END
WHERE owner_id IS NULL
"""

# The old constraint was global, so at most one row per fingerprint exists and rescoping
# cannot create a duplicate. Kept as a defensive dedupe in case a database was patched by
# hand between releases.
DEDUPE = """
DELETE FROM clause_patterns a
USING clause_patterns b
WHERE a.owner_id = b.owner_id
  AND a.fingerprint = b.fingerprint
  AND a.id <> b.id
  AND (a.times_matched, a.confidence, a.id::text)
    < (b.times_matched, b.confidence, b.id::text)
"""


def upgrade() -> None:
    op.add_column('clause_patterns', sa.Column('owner_id', sa.String(64), nullable=True))
    op.execute(CLASSIFY_EXISTING)
    op.alter_column('clause_patterns', 'owner_id', nullable=False)

    op.drop_constraint('uq_clause_patterns_fingerprint', 'clause_patterns', type_='unique')
    op.execute(DEDUPE)
    op.create_unique_constraint(
        'uq_clause_patterns_owner_fingerprint',
        'clause_patterns',
        ['owner_id', 'fingerprint'],
    )
    # Every RAG read is `owner_id IN (:owner, 'global')`.
    op.create_index('ix_clause_patterns_owner_id', 'clause_patterns', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_clause_patterns_owner_id', table_name='clause_patterns')
    op.drop_constraint(
        'uq_clause_patterns_owner_fingerprint', 'clause_patterns', type_='unique'
    )
    # Reverting to a global unique constraint means collapsing per-owner duplicates first.
    op.execute("""
    DELETE FROM clause_patterns a
    USING clause_patterns b
    WHERE a.fingerprint = b.fingerprint
      AND a.id <> b.id
      AND (a.times_matched, a.confidence, a.id::text)
        < (b.times_matched, b.confidence, b.id::text)
    """)
    op.create_unique_constraint(
        'uq_clause_patterns_fingerprint', 'clause_patterns', ['fingerprint']
    )
    op.drop_column('clause_patterns', 'owner_id')
