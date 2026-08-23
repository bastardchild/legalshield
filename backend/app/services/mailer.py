import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def send_counter_draft(
    contract_id: str,
    recipient_email: str | None,
    counter_draft_text: str,
) -> dict:
    """
    Stub email sender — logs the intent and returns a record dict.
    Replace with real SMTP / SendGrid call if needed.
    """
    timestamp = datetime.now(UTC).isoformat()
    logger.info(
        f"[MAILER STUB] Contract {contract_id} → recipient={recipient_email} "
        f"at {timestamp}. Draft length: {len(counter_draft_text)} chars."
    )
    return {
        "status": "stub",
        "contract_id": contract_id,
        "recipient_email": recipient_email,
        "sent_at": timestamp,
        "note": "Email delivery stubbed — log only. Set up SMTP to enable real sending.",
    }
