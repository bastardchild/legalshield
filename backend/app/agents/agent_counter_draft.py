import json
import logging
import re
from pathlib import Path
from datetime import datetime

from app.services.llm_client import chat_completion

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "counter_draft.txt"


def _load_prompt(contract_text: str, risk_findings: str, tax_findings: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template
        .replace("{{ contract_text }}", contract_text)
        .replace("{{ risk_findings }}", risk_findings)
        .replace("{{ tax_findings }}", tax_findings)
    )


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if match:
        raw = match.group(1)
    return json.loads(raw)


async def run_counter_draft_agent(
    contract_text: str,
    risk_findings: dict,
    tax_findings: dict,
) -> dict:
    """
    Sub-agent C: Counter-Draft Composer.
    Depends on results from agents A and B.
    Returns dict with keys: counter_draft, summary_of_changes, negotiation_notes.
    """
    started_at = datetime.utcnow().isoformat()
    logger.info(f"[CounterDraftAgent] Starting at {started_at}")

    risk_str = json.dumps(risk_findings.get("findings", []), ensure_ascii=False, indent=2)
    tax_str = json.dumps(tax_findings.get("findings", []), ensure_ascii=False, indent=2)

    prompt = _load_prompt(contract_text, risk_str, tax_str)
    messages = [
        {"role": "system", "content": "You are a legal contract drafter. Return only valid JSON."},
        {"role": "user", "content": prompt},
    ]

    raw = await chat_completion(messages, temperature=0.3, max_tokens=6000)
    result = _parse_json_response(raw)

    finished_at = datetime.utcnow().isoformat()
    logger.info(f"[CounterDraftAgent] Done at {finished_at}.")
    result["_started_at"] = started_at
    result["_finished_at"] = finished_at
    return result
