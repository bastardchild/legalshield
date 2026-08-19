import json
import logging
import re
from pathlib import Path
from datetime import datetime

from app.services.llm_client import chat_completion

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "tax_compliance.txt"


def _load_prompt(contract_text: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{ contract_text }}", contract_text)


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    if match:
        raw = match.group(1)
    return json.loads(raw)


async def run_tax_compliance_agent(contract_text: str) -> dict:
    """
    Sub-agent B: Tax / Local Compliance Checker.
    Returns dict with key 'findings' containing list of compliance issue objects.
    """
    started_at = datetime.utcnow().isoformat()
    logger.info(f"[TaxComplianceAgent] Starting at {started_at}")

    prompt = _load_prompt(contract_text)
    messages = [
        {"role": "system", "content": "You are a tax and compliance expert. Return only valid JSON."},
        {"role": "user", "content": prompt},
    ]

    raw = await chat_completion(messages, temperature=0.1, max_tokens=3000)
    result = _parse_json_response(raw)

    finished_at = datetime.utcnow().isoformat()
    logger.info(
        f"[TaxComplianceAgent] Done at {finished_at}. "
        f"Findings: {len(result.get('findings', []))}"
    )
    result["_started_at"] = started_at
    result["_finished_at"] = finished_at
    return result
