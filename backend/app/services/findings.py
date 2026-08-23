"""
Normalisation and sanitisation of LLM output.

Two jobs:

1. Coerce model findings into the shape the templates and the skill store expect, so a
   single malformed entry cannot break rendering or poison `clause_patterns`.
2. Fence untrusted contract text before it enters a prompt, so a contract cannot issue
   instructions to the model.
"""
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

# Only these have badge/stripe styling in app.css and meaning in the skill store.
VALID_SEVERITIES = ("critical", "high", "medium", "low")
DEFAULT_SEVERITY = "medium"

# Common model variants, including Bahasa Indonesia, mapped onto the canonical set.
_SEVERITY_ALIASES = {
    "kritis": "critical", "sangat tinggi": "critical", "severe": "critical", "critical": "critical",
    "tinggi": "high", "high": "high", "major": "high",
    "sedang": "medium", "medium": "medium", "moderate": "medium", "normal": "medium",
    "rendah": "low", "low": "low", "minor": "low", "info": "low",
}

MAX_TEXT_CHARS = 2000

# Sentinel used to fence untrusted input. Must not appear in the payload.
_FENCE = "===== UNTRUSTED CONTRACT TEXT ====="


def normalize_severity(value: object) -> str:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _SEVERITY_ALIASES:
            return _SEVERITY_ALIASES[key]
    return DEFAULT_SEVERITY


def normalize_confidence(value: object) -> float:
    """
    Coerce confidence to a float in [0.0, 1.0].

    Models return 0.9, "0.9", "90%", or 90. Anything uninterpretable becomes 0.0 so it
    falls below the skill-store threshold rather than being trusted by default.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        num = float(value)
    elif isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            num = float(text)
        except ValueError:
            return 0.0
        if value.strip().endswith("%"):
            num /= 100.0
    else:
        return 0.0

    if num > 1.0:
        # Treat 90 as 90%, but clamp anything absurd.
        num = num / 100.0 if num <= 100.0 else 1.0
    return max(0.0, min(1.0, num))


def _clean_text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text[:limit] if len(text) > limit else text


def normalize_findings(raw: object, *, type_field: str, agent_label: str) -> list[dict]:
    """
    Return a list of well-formed findings.

    `type_field` is "clause_type" for the risk agent and "issue_type" for the tax agent.
    Entries that are not dicts, or that carry no usable text at all, are dropped with a
    warning rather than propagated.
    """
    if not isinstance(raw, list):
        logger.warning(f"[{agent_label}] Expected 'findings' to be a list, got {type(raw).__name__}.")
        return []

    cleaned: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.warning(f"[{agent_label}] Dropping finding {i}: not an object ({type(item).__name__}).")
            continue

        finding = {
            type_field: _clean_text(item.get(type_field), 128) or "other",
            "severity": normalize_severity(item.get("severity")),
            "original_text": _clean_text(item.get("original_text")),
            "explanation": _clean_text(item.get("explanation")),
            "recommendation": _clean_text(item.get("recommendation")),
        }
        if type_field == "clause_type":
            finding["confidence"] = normalize_confidence(item.get("confidence"))
        if item.get("applicable_regulation"):
            finding["applicable_regulation"] = _clean_text(item.get("applicable_regulation"), 256)

        if not (finding["explanation"] or finding["recommendation"] or finding["original_text"]):
            logger.warning(f"[{agent_label}] Dropping finding {i}: no usable content.")
            continue

        cleaned.append(finding)

    dropped = len(raw) - len(cleaned)
    if dropped:
        logger.info(f"[{agent_label}] Normalised {len(cleaned)} findings, dropped {dropped}.")
    return cleaned


def normalize_counter_draft(raw: dict, *, agent_label: str) -> dict:
    """Coerce the counter-draft agent's payload into its three expected keys."""
    draft = raw.get("counter_draft")
    if isinstance(draft, list):
        draft = "\n".join(str(part) for part in draft)
    draft = draft.strip() if isinstance(draft, str) else ""

    summary = raw.get("summary_of_changes")
    if isinstance(summary, str):
        summary = [summary]
    elif not isinstance(summary, list):
        summary = []
    summary = [_clean_text(s, 500) for s in summary if _clean_text(s, 500)]

    notes = raw.get("negotiation_notes")
    notes = _clean_text(notes, MAX_TEXT_CHARS) if notes else ""

    if not draft:
        logger.warning(f"[{agent_label}] Counter-draft text is empty.")

    return {
        "counter_draft": draft,
        "summary_of_changes": summary,
        "negotiation_notes": notes,
    }


def truncate_contract(text: str) -> tuple[str, bool]:
    """
    Cap contract text at `max_contract_chars`.

    Returns (text, was_truncated). Without this a large PDF silently blows past the
    model's context window; agent C is worst affected since it also receives both
    findings sets alongside the original text.
    """
    limit = get_settings().max_contract_chars
    if len(text) <= limit:
        return text, False
    logger.warning(f"Contract text truncated from {len(text)} to {limit} chars for LLM input.")
    return text[:limit], True


def wrap_untrusted(text: str) -> str:
    """
    Fence contract text so the model treats it as data, not instructions.

    The sentinel is stripped from the payload first, otherwise a crafted contract could
    close the fence early and append its own directives. This is mitigation, not a
    guarantee — see KNOWN_ISSUES #6.
    """
    safe = text.replace(_FENCE, "[removed]")
    return f"{_FENCE}\n{safe}\n{_FENCE}"
