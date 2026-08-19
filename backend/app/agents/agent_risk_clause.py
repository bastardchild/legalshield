import json
import logging
import re
from pathlib import Path
from datetime import datetime

from app.services.llm_client import chat_completion
from app.services.skill_store import get_active_patterns

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "risk_clause.txt"


def _load_prompt(contract_text: str, known_patterns: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{ contract_text }}", contract_text).replace(
        "{{ known_patterns }}", known_patterns
    )


def _parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if match:
        raw = match.group(1)
    return json.loads(raw)


async def run_risk_clause_agent(contract_text: str) -> dict:
    """
    Sub-agent A: Risk Clause Detector.
    Returns dict with key 'findings' containing list of risky clause objects.
    """
    started_at = datetime.utcnow().isoformat()
    logger.info(f"[RiskClauseAgent] Starting at {started_at}")

    # Load known patterns for RAG context
    try:
        patterns = await get_active_patterns()
        known_patterns_text = "\n".join(
            f"- [{p['severity'].upper()}] {p['pattern_name']}: {p['description']} (example: {p['example_text'][:120]})"
            for p in patterns
        ) or "No known patterns yet."
    except Exception as e:
        logger.warning(f"Could not load skill store patterns: {e}")
        known_patterns_text = "No known patterns yet."

    prompt = _load_prompt(contract_text, known_patterns_text)
    messages = [
        {"role": "system", "content": "You are a legal contract analyst. Return only valid JSON."},
        {"role": "user", "content": prompt},
    ]

    raw = await chat_completion(messages, temperature=0.1, max_tokens=4096)
    result = _parse_json_response(raw)

    finished_at = datetime.utcnow().isoformat()
    logger.info(
        f"[RiskClauseAgent] Done at {finished_at}. "
        f"Findings: {len(result.get('findings', []))}"
    )
    result["_started_at"] = started_at
    result["_finished_at"] = finished_at
    return result
