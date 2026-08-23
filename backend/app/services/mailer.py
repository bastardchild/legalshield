"""
Counter-draft delivery by email.

Stub mode is the default and stays the default: with `SMTP_HOST` empty the send is logged
and nothing leaves the process, which is what the MVP demo relies on. Configure SMTP and
the same function delivers for real.

Three decisions worth stating, because each has a failure mode attached:

* **Failures are returned, never raised.** The caller records a `negotiation_sends` row
  before deciding the HTTP status, so the row is an audit trail of what was *attempted*.
  A raised exception would lose the attempt entirely.
* **The send runs in a thread.** `smtplib` is blocking, and the route is async; calling it
  directly would stall the whole event loop for the duration of the SMTP conversation —
  up to `smtp_timeout_seconds` if the server is unresponsive.
* **The recipient address is validated before use.** It arrives from a JSON body, and an
  address containing CR/LF would let a caller inject extra SMTP headers (a bcc, a forged
  From) into the message.

The draft is sent twice on purpose: inline in the body so it is readable without opening
anything, and as a `.txt` attachment because the drafts run to 20 KB and the recipient's
next step is to edit one.
"""
import asyncio
import logging
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)

STATUS_STUB = "stub"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

# Deliberately permissive: this guards against header injection and obvious typos, not
# against every RFC 5322 subtlety. Rejecting valid-but-unusual addresses would be worse.
_EMAIL_RE = re.compile(r"^[^@\s,;:<>\"\\]+@[^@\s,;:<>\"\\]+\.[A-Za-z]{2,}$")

SUBJECT_TEMPLATE = "Draft Kontrak Balasan — LegalShield Agent"
BODY_TEMPLATE = """\
Berikut draft kontrak balasan yang disusun otomatis oleh LegalShield Agent.

Referensi kontrak: {contract_id}
Dibuat: {timestamp}

Draft ini dihasilkan oleh sistem otomatis dan bukan nasihat hukum. Tinjau isinya sebelum
dikirim ke pihak lain.

--------------------------------------------------------------------------------
{draft}
--------------------------------------------------------------------------------
"""


ATTACHMENT_TEMPLATE = "draft-kontrak-{contract_id}.txt"


def smtp_configured() -> bool:
    return bool(get_settings().smtp_host.strip())


def valid_email(address: str | None) -> bool:
    """
    True if the address is safe to put in a header.

    The length cap and the CR/LF exclusion in the pattern are the security-relevant parts:
    a newline in a header value lets the caller append headers of their own.
    """
    if not address:
        return False
    address = address.strip()
    if len(address) > 254 or "\r" in address or "\n" in address:
        return False
    return bool(_EMAIL_RE.match(address))


def build_message(contract_id: str, recipient: str, draft: str) -> EmailMessage:
    settings = get_settings()
    sender = settings.smtp_from.strip() or settings.smtp_username.strip()

    message = EmailMessage()
    message["Subject"] = SUBJECT_TEMPLATE
    # set_content escapes nothing, but EmailMessage refuses header values containing
    # newlines, so a crafted address cannot smuggle extra headers past this point.
    message["From"] = f"{settings.smtp_from_name} <{sender}>" if sender else recipient
    message["To"] = recipient
    message.set_content(
        BODY_TEMPLATE.format(
            contract_id=contract_id,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            draft=draft or "(draft kosong)",
        )
    )
    # Also attached, because a 20 KB draft is something the recipient edits rather than
    # reads once. add_attachment turns the message into multipart/mixed, so the plain part
    # has to be reached via get_body() from here on.
    message.add_attachment(
        draft or "(draft kosong)",
        subtype="plain",
        filename=ATTACHMENT_TEMPLATE.format(contract_id=contract_id),
    )
    return message


def body_text(message: EmailMessage) -> str:
    """The human-readable part of a built message. The message is multipart."""
    part = message.get_body(("plain",))
    return part.get_content() if part else ""


def _deliver(message: EmailMessage) -> None:
    """Blocking SMTP conversation. Runs in a worker thread; raises on any failure."""
    settings = get_settings()
    host, port = settings.smtp_host.strip(), settings.smtp_port
    timeout = settings.smtp_timeout_seconds

    if settings.smtp_use_ssl:
        client = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)

    try:
        client.ehlo()
        if settings.smtp_use_tls and not settings.smtp_use_ssl:
            client.starttls()
            # A second EHLO is required after STARTTLS: the server's advertised
            # capabilities (notably AUTH) are only valid for the encrypted session.
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            # The message is already accepted at this point; a failed QUIT is noise.
            client.close()


async def send_counter_draft(
    contract_id: str,
    recipient_email: str | None,
    counter_draft_text: str,
) -> dict:
    """
    Deliver the counter-draft, or log the intent when SMTP is not configured.

    Returns a record dict whose `status` is one of `stub`, `sent`, or `failed`. Never raises:
    the caller persists this outcome before choosing an HTTP status, so a delivery failure
    must remain a value rather than an exception.
    """
    timestamp = datetime.now(UTC).isoformat()
    record = {
        "status": STATUS_STUB,
        "contract_id": contract_id,
        "recipient_email": recipient_email,
        "sent_at": timestamp,
        "note": "",
    }

    if not smtp_configured():
        logger.info(
            f"[Mailer] SMTP not configured; logging only. Contract {contract_id} "
            f"→ recipient={recipient_email}, draft={len(counter_draft_text)} chars."
        )
        record["note"] = "Email delivery stubbed — log only. Set SMTP_HOST to enable sending."
        return record

    if not valid_email(recipient_email):
        logger.info(f"[Mailer] Refusing to send to invalid address {recipient_email!r}.")
        record["status"] = STATUS_FAILED
        record["error"] = "Alamat email penerima tidak valid."
        record["note"] = record["error"]
        return record

    recipient = recipient_email.strip()
    try:
        message = build_message(contract_id, recipient, counter_draft_text)
        # smtplib blocks; running it inline would stall every other request on this loop.
        await asyncio.to_thread(_deliver, message)
    except Exception as e:
        # Includes SMTPException, socket.timeout, OSError, and the ValueError EmailMessage
        # raises on a header it refuses to encode.
        logger.error(f"[Mailer] Delivery failed for contract {contract_id}: {e!r}")
        record["status"] = STATUS_FAILED
        # The exception text can name the SMTP host and the authenticating user, so it is
        # logged but not returned to the client.
        record["error"] = "Pengiriman email gagal. Periksa konfigurasi SMTP."
        record["note"] = record["error"]
        return record

    logger.info(f"[Mailer] Sent counter-draft for contract {contract_id} to {recipient}.")
    record["status"] = STATUS_SENT
    record["note"] = "Email terkirim."
    return record
