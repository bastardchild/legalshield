"""
Bootstrap the skill store and the legal-reference index from the shipped seed data
(KNOWN_ISSUES #18).

`seed/dataset1.json` (100 labelled clauses) and `seed/datasetpasal1.json` (50 Indonesian
regulations) were in the repository but nothing read them, so the risk agent started with
an empty RAG context on every fresh deployment and the tax agent had no citation list.

Loading is idempotent: clauses are keyed by the same content fingerprint the skill store
uses at runtime, so re-running only tops up what is missing.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ClausePattern
from app.services.findings import normalize_confidence, normalize_severity
from app.services.skill_store import GLOBAL_OWNER, fingerprint

logger = logging.getLogger(__name__)

CLAUSE_DATASET = "dataset1.json"
LEGAL_DATASET = "datasetpasal1.json"

# Seeded patterns start below a runtime-confirmed match so genuine observations outrank
# them in the `times_matched DESC` ordering used to build RAG context.
SEED_TIMES_MATCHED = 0


def _seed_path(name: str) -> Path:
    return Path(get_settings().seed_dir) / name


def _load_json(name: str) -> list[dict]:
    path = _seed_path(name)
    if not path.exists():
        logger.warning(f"[Seed] {path} not found; skipping.")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"[Seed] Could not read {path}: {e}")
        return []
    if not isinstance(data, list):
        logger.error(f"[Seed] {path} must contain a JSON array, got {type(data).__name__}.")
        return []
    return [r for r in data if isinstance(r, dict)]


def clause_patterns_from_dataset() -> list[dict]:
    """
    Flatten dataset1.json into ClausePattern-shaped dicts.

    Each record has one snippet and one or more risk_findings. The snippet is the example
    text (there is no per-finding snippet), so records with several findings produce
    several patterns that share it — differentiated by clause_type in the fingerprint.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    for record in _load_json(CLAUSE_DATASET):
        snippet = (record.get("contract_snippet") or "").strip()
        if not snippet:
            continue

        findings = record.get("risk_findings") or []
        if not isinstance(findings, list) or not findings:
            # Still worth storing: category + severity describe a real risky clause.
            findings = [
                {
                    "clause_type": record.get("category"),
                    "severity": record.get("severity"),
                    "explanation": record.get("counter_suggestion"),
                }
            ]

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            clause_type = (
                finding.get("clause_type") or record.get("category") or "other"
            ).strip() or "other"
            severity = normalize_severity(finding.get("severity") or record.get("severity"))
            description = (finding.get("explanation") or "").strip()
            recommendation = (finding.get("recommendation") or "").strip()
            if recommendation:
                description = f"{description} Saran: {recommendation}".strip()
            if not description:
                description = f"Klausul {clause_type} berisiko ({severity})."

            fp = fingerprint(clause_type, snippet)
            if fp in seen:
                continue
            seen.add(fp)

            rows.append(
                {
                    # Curated data goes to the shared global bucket, which every owner
                    # reads and nothing writes at runtime, so it cannot be poisoned.
                    "owner_id": GLOBAL_OWNER,
                    "pattern_name": f"{clause_type}:{severity}",
                    "fingerprint": fp,
                    "description": description[:500],
                    "example_text": snippet[:500],
                    "severity": severity,
                    "times_matched": SEED_TIMES_MATCHED,
                    "confidence": normalize_confidence(finding.get("confidence")) or 0.9,
                    "is_active": True,
                }
            )

    return rows


async def seed_clause_patterns(db) -> int:
    """
    Insert any seed pattern not already present. Returns the number of rows added.

    Existing rows are left untouched: their `times_matched` and `confidence` reflect real
    observations and must not be reset to seed defaults. Only the global bucket is
    considered when deciding what is missing — a private copy of the same clause under some
    owner's id must not suppress the curated row everyone else reads.
    """
    rows = clause_patterns_from_dataset()
    if not rows:
        return 0

    existing = set(
        (
            await db.execute(
                select(ClausePattern.fingerprint).where(
                    ClausePattern.owner_id == GLOBAL_OWNER
                )
            )
        )
        .scalars()
        .all()
    )
    new_rows = [r for r in rows if r["fingerprint"] not in existing]
    if not new_rows:
        logger.info(f"[Seed] Skill store already has all {len(rows)} seed pattern(s).")
        return 0

    db.add_all([ClausePattern(**r) for r in new_rows])
    await db.commit()
    logger.info(f"[Seed] Added {len(new_rows)} clause pattern(s) from {CLAUSE_DATASET}.")
    return len(new_rows)


async def clause_pattern_count(db) -> int:
    return int((await db.execute(select(func.count(ClausePattern.id)))).scalar() or 0)


@lru_cache(maxsize=1)
def legal_references() -> list[dict]:
    """
    Regulations from datasetpasal1.json, in a shape convenient for prompt injection.

    Cached: the file is static and the tax agent reads it on every run.
    """
    refs: list[dict] = []
    for record in _load_json(LEGAL_DATASET):
        kode = (record.get("kode") or "").strip()
        if not kode:
            continue
        pasal = record.get("pasal_relevan") or []
        if not isinstance(pasal, list):
            pasal = [str(pasal)]
        refs.append(
            {
                "kode": kode,
                "nama": (record.get("nama_lengkap") or kode).strip(),
                "jenis": (record.get("jenis") or "").strip(),
                "topik": (record.get("topik") or "").strip(),
                "relevansi": (record.get("relevansi_kontrak") or "").strip(),
                "pasal": [str(p) for p in pasal][:12],
                "url": (record.get("url_resmi") or "").strip(),
            }
        )
    logger.info(f"[Seed] Loaded {len(refs)} legal reference(s) from {LEGAL_DATASET}.")
    return refs


def legal_references_text(limit: int | None = None) -> str:
    """
    Compact citation list for the tax agent's prompt.

    Capped, because this text is prepended to every tax analysis and the full 50 records
    would consume context that the contract itself needs.
    """
    if limit is None:
        limit = get_settings().max_legal_references
    refs = legal_references()[:limit]
    if not refs:
        return "Tidak ada daftar referensi hukum yang dimuat."

    lines = []
    for ref in refs:
        pasal = f" (Pasal {', '.join(ref['pasal'])})" if ref["pasal"] else ""
        topik = f" — {ref['topik']}" if ref["topik"] else ""
        lines.append(f"- {ref['kode']}{pasal}{topik}")
    return "\n".join(lines)
