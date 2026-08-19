import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import ClausePattern

logger = logging.getLogger(__name__)

# Minimum confidence to consider saving as new pattern
CONFIDENCE_THRESHOLD = 0.75
# Simple similarity: if pattern_name already exists (case-insensitive), don't duplicate
SIMILARITY_CHECK_FIELD = "pattern_name"


async def get_active_patterns(db: AsyncSession | None = None) -> list[dict]:
    """
    Retrieve all active clause patterns for use as RAG context in the risk agent.
    Opens its own session if none provided.
    """
    from app.db.session import AsyncSessionLocal
    close_after = False
    if db is None:
        db = AsyncSessionLocal()
        close_after = True
    try:
        result = await db.execute(
            select(ClausePattern)
            .where(ClausePattern.is_active == True)
            .order_by(ClausePattern.times_matched.desc())
        )
        patterns = result.scalars().all()
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
            for p in patterns
        ]
    finally:
        if close_after:
            await db.close()


async def save_new_patterns(db: AsyncSession, findings: list[dict]):
    """
    For each high-confidence finding from the risk agent:
    - If a matching pattern already exists (by clause_type + similar name), increment times_matched.
    - Otherwise, insert a new ClausePattern row.
    """
    for finding in findings:
        confidence = float(finding.get("confidence", 0.0))
        if confidence < CONFIDENCE_THRESHOLD:
            continue

        clause_type = finding.get("clause_type", "other")
        explanation = finding.get("explanation", "")
        original_text = finding.get("original_text", "")
        severity = finding.get("severity", "medium")
        pattern_name = f"{clause_type}:{severity}"

        # Check for existing match by pattern_name
        result = await db.execute(
            select(ClausePattern).where(
                ClausePattern.pattern_name == pattern_name,
                ClausePattern.is_active == True,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.times_matched += 1
            existing.updated_at = datetime.now(timezone.utc)
            logger.info(
                f"[SkillStore] Pattern '{pattern_name}' matched — times_matched={existing.times_matched}"
            )
        else:
            new_pattern = ClausePattern(
                pattern_name=pattern_name,
                description=explanation[:500],
                example_text=original_text[:500],
                severity=severity,
                times_matched=1,
                confidence=confidence,
                is_active=True,
            )
            db.add(new_pattern)
            logger.info(f"[SkillStore] New pattern saved: '{pattern_name}' (confidence={confidence:.2f})")

    await db.commit()
