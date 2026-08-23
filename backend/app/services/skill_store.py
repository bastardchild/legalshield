"""
Self-improving clause-pattern store.

Patterns are **per owner** (KNOWN_ISSUES #6). Prompt injection is mitigated by fencing but
not eliminated, and this store was the mechanism that made a single successful injection
permanent: a crafted contract yielding a high-confidence "finding" was written to a global
table and then injected as RAG context into every other user's later analysis.

Two reserved owner ids, neither of which `security.new_owner_id()` can produce (it always
returns 32 hex characters):

* `global` — curated seed data. Readable by everyone, never written at runtime.
* `legacy` — learned before ownership existed. Retained for auditing, never read, because
  there is no way to know which of those rows came from a hostile upload.

A reader therefore sees `own patterns + global`, and writes only ever land under its own id.
"""
import hashlib
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClausePattern

logger = logging.getLogger(__name__)

# Minimum confidence before a finding is trusted enough to enter the store.
CONFIDENCE_THRESHOLD = 0.75
# Default cap on patterns returned for RAG context.
DEFAULT_PATTERN_LIMIT = 40

# Reserved owner ids. Unreachable as real owners: new_owner_id() is always 32 hex chars.
GLOBAL_OWNER = "global"
LEGACY_OWNER = "legacy"
RESERVED_OWNERS = (GLOBAL_OWNER, LEGACY_OWNER)


def fingerprint(clause_type: str, example_text: str) -> str:
    """
    Content fingerprint for deduplication.

    Keying on `clause_type:severity` (the previous scheme) collapsed every non-compete
    into one row, so the first example seen was the only one ever stored. Hashing the
    normalised example text instead keeps genuinely different clauses apart while still
    recognising the same clause across uploads.
    """
    normalized = re.sub(r"\s+", " ", example_text or "").strip().lower()
    digest = hashlib.sha256(f"{clause_type}|{normalized}".encode()).hexdigest()
    return digest[:32]


def readable_owners(owner_id: str | None) -> list[str]:
    """
    Owner ids whose patterns this caller may see.

    `legacy` is never included, and a caller cannot request another owner's patterns by
    passing a reserved id: `global` is added unconditionally and anything else is dropped.
    """
    owners = [GLOBAL_OWNER]
    if owner_id and owner_id not in RESERVED_OWNERS:
        owners.append(owner_id)
    return owners


async def get_active_patterns(
    db: AsyncSession | None = None,
    limit: int = DEFAULT_PATTERN_LIMIT,
    owner_id: str | None = None,
) -> list[dict]:
    """
    Active clause patterns for RAG context, most-matched first.

    `limit` is mandatory in effect: the risk agent injects these into every prompt, so an
    unbounded result set grows the prompt (and cost) until the context window overflows.

    With no `owner_id` only the curated global patterns are returned. That is the safe
    default — a caller that forgot to pass an owner gets less context, not someone else's.
    """
    from app.db.session import AsyncSessionLocal

    close_after = db is None
    if db is None:
        db = AsyncSessionLocal()
    try:
        owners = readable_owners(owner_id)
        result = await db.execute(
            select(ClausePattern)
            .where(
                ClausePattern.is_active.is_(True),
                ClausePattern.owner_id.in_(owners),
            )
            # Private rows first at equal match counts: a clause this owner has actually
            # seen is better evidence than a generic labelled example of the same thing.
            .order_by(
                ClausePattern.times_matched.desc(),
                (ClausePattern.owner_id == GLOBAL_OWNER).asc(),
                ClausePattern.created_at.desc(),
            )
            # Over-fetch so the fingerprint dedupe below cannot shrink the result set
            # under `limit` when an owner has privately re-observed a seeded clause.
            .limit(limit * 2)
        )

        patterns: list[dict] = []
        seen: set[str] = set()
        for p in result.scalars().all():
            if p.fingerprint in seen:
                # Same clause held both privately and globally; the ordering above already
                # put the more informative row first.
                continue
            seen.add(p.fingerprint)
            patterns.append(
                {
                    "id": str(p.id),
                    "pattern_name": p.pattern_name,
                    "description": p.description,
                    "example_text": p.example_text,
                    "severity": p.severity,
                    "times_matched": p.times_matched,
                    "confidence": p.confidence,
                }
            )
            if len(patterns) == limit:
                break
        return patterns
    finally:
        if close_after:
            await db.close()


async def save_new_patterns(
    db: AsyncSession,
    findings: list[dict],
    owner_id: str | None = None,
) -> int:
    """
    Persist high-confidence findings as reusable patterns, owned by `owner_id`.

    Findings are expected to be pre-normalised by `services.findings.normalize_findings`,
    so severity and confidence are already canonical. Returns the number of new rows.

    Writing is skipped entirely without a real `owner_id`: an unattributed pattern would
    have to go somewhere, and the only shared bucket is `global`, which must stay curated.
    """
    if not owner_id or owner_id in RESERVED_OWNERS:
        logger.warning(
            "[SkillStore] Refusing to store patterns without an owner "
            f"(owner_id={owner_id!r}); they would land in the shared global bucket."
        )
        return 0

    inserted = 0
    for finding in findings:
        confidence = float(finding.get("confidence", 0.0) or 0.0)
        if confidence < CONFIDENCE_THRESHOLD:
            continue

        clause_type = (finding.get("clause_type") or "other").strip() or "other"
        example_text = (finding.get("original_text") or "").strip()
        explanation = (finding.get("explanation") or "").strip()
        severity = finding.get("severity") or "medium"

        # Without example text there is nothing to match on later, and nothing useful to
        # show the model as an example.
        if not example_text:
            continue

        fp = fingerprint(clause_type, example_text)
        # Scoped to this owner only. Bumping a matching global row's counter would be a
        # runtime write to shared state, letting one user reorder everyone's RAG context;
        # the owner gets their own row instead, and the read path dedupes the overlap.
        existing = (
            await db.execute(
                select(ClausePattern).where(
                    ClausePattern.fingerprint == fp,
                    ClausePattern.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.times_matched += 1
            existing.confidence = max(existing.confidence, confidence)
            existing.updated_at = datetime.now(UTC)
            logger.info(
                f"[SkillStore] Pattern '{existing.pattern_name}' matched again "
                f"(times_matched={existing.times_matched})"
            )
            continue

        db.add(
            ClausePattern(
                owner_id=owner_id,
                pattern_name=f"{clause_type}:{severity}",
                fingerprint=fp,
                description=explanation[:500],
                example_text=example_text[:500],
                severity=severity,
                times_matched=1,
                confidence=confidence,
                is_active=True,
            )
        )
        inserted += 1
        logger.info(
            f"[SkillStore] New pattern '{clause_type}:{severity}' "
            f"(confidence={confidence:.2f}, fp={fp[:8]})"
        )

    await db.commit()
    return inserted
