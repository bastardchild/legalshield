import hashlib
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClausePattern

logger = logging.getLogger(__name__)

# Minimum confidence before a finding is trusted enough to enter the shared store.
CONFIDENCE_THRESHOLD = 0.75
# Default cap on patterns returned for RAG context.
DEFAULT_PATTERN_LIMIT = 40


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


async def get_active_patterns(
    db: AsyncSession | None = None,
    limit: int = DEFAULT_PATTERN_LIMIT,
) -> list[dict]:
    """
    Active clause patterns for RAG context, most-matched first.

    `limit` is mandatory in effect: the risk agent injects these into every prompt, so an
    unbounded result set grows the prompt (and cost) until the context window overflows.
    """
    from app.db.session import AsyncSessionLocal

    close_after = db is None
    if db is None:
        db = AsyncSessionLocal()
    try:
        result = await db.execute(
            select(ClausePattern)
            .where(ClausePattern.is_active.is_(True))
            .order_by(ClausePattern.times_matched.desc(), ClausePattern.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": str(p.id),
                "pattern_name": p.pattern_name,
                "description": p.description,
                "example_text": p.example_text,
                "severity": p.severity,
                "times_matched": p.times_matched,
                "confidence": p.confidence,
            }
            for p in result.scalars().all()
        ]
    finally:
        if close_after:
            await db.close()


async def save_new_patterns(db: AsyncSession, findings: list[dict]) -> int:
    """
    Persist high-confidence findings as reusable patterns.

    Findings are expected to be pre-normalised by `services.findings.normalize_findings`,
    so severity and confidence are already canonical. Returns the number of new rows.

    Note: patterns are global, so a poisoned upload can influence later analyses.
    The confidence threshold is the only gate today — see KNOWN_ISSUES #6.
    """
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
        existing = (
            await db.execute(select(ClausePattern).where(ClausePattern.fingerprint == fp))
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
