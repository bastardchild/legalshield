"""
WhatsApp link notification via Fonnte.

Stub mode is the default: with `FONNTE_TOKEN` empty the send is logged and nothing
leaves the process. Configure Fonnte and the same function delivers for real.

Fonnte API: POST {FONNTE_BASE_URL}/send  (default https://api.fonnte.com/send)
  Headers: Authorization: <token>  (raw token, NOT Bearer)
  Body (form-encoded or JSON): target, message, countryCode, delay, typing
  See https://docs.fonnte.com/api-send-message/

Three decisions worth stating:

* **Failures are returned, never raised.** The caller records a `whatsapp_sends` row
  before deciding any HTTP status, so the row is an audit trail of what was
  *attempted*. A raised exception would lose the attempt entirely.
* **The send runs in a thread when using sync httpx**, or natively async with
  AsyncClient. Either way the event loop is not stalled for the duration of the
  HTTP call — up to `fonnte_timeout_seconds` if the API is unresponsive.
* **The phone number is validated before use.** It arrives from a Form field and
  may contain CR/LF injection attempts or non-numeric junk.

The message is link-only: just the result URL, no draft text. The draft lives
at /contracts/{id} and is owner-scoped via the HMAC cookie.
"""

import asyncio
import logging
import re

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

STATUS_STUB = "stub"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

# Stripped value must be 8-15 digits after optional leading + and after removing
# spaces/dashes/parens. This mirrors E.164 without enforcing a specific country.
_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)]+")
_DIGITS_RE = re.compile(r"^\+?[0-9]{8,15}$")


def fonnte_configured() -> bool:
    return bool(get_settings().fonnte_token.strip())


def normalize_phone(raw: str | None) -> str:
    """
    Strip spaces/dashes/parens, handle leading 0 → 62, and +62 → 62.
    Returns digits-only string with country code (e.g. 628123456789).
    Caller must validate first via valid_phone.
    """
    if not raw:
        return ""
    s = raw.strip()
    # Remove spaces, dashes, parens
    s = _PHONE_STRIP_RE.sub("", s)
    # Remove leading +
    if s.startswith("+"):
        s = s[1:]
    # Leading 0 → 62 (Indonesian default)
    if s.startswith("0"):
        s = "62" + s[1:]
    return s


def valid_phone(raw: str | None) -> bool:
    """
    True if the phone is safe and plausible.

    Rejects CR/LF (header injection), empty, and non-numeric/short/long values.
    Normalizes before length check so '08 12-3456 7890' is accepted.
    """
    if not raw:
        return False
    if "\r" in raw or "\n" in raw:
        return False
    s = raw.strip()
    if not s:
        return False
    # Strip formatting chars, then check digits
    stripped = _PHONE_STRIP_RE.sub("", s)
    # Allow leading +
    if stripped.startswith("+"):
        stripped = stripped[1:]
    # After stripping formatting, must be all digits
    if not stripped.isdigit():
        return False
    # Normalize 0 → 62 for length check
    if stripped.startswith("0"):
        stripped = "62" + stripped[1:]
    # 8-15 digits (E.164 range, country code included)
    if not (8 <= len(stripped) <= 15):
        return False
    # Final regex on original stripped form (with optional +)
    # Re-check with + for the regex path
    check = raw.strip()
    check = _PHONE_STRIP_RE.sub("", check)
    return bool(_DIGITS_RE.match(check))


def _build_link_message(contract_id: str, result_url: str, filename: str = "") -> str:
    """
    Link-only message. No draft text — just the result URL.
    Filename is not interpolated as free text to avoid injection of draft content.
    """
    return (
        f"Analisis kontrak selesai.\n"
        f"Lihat hasil: {result_url}\n"
        f"\n"
        f"\u2014 LegalShield Agent"
    )


def build_fonnte_payload(contract_id: str, target: str, result_url: str, filename: str = "") -> dict:
    """
    Build the Fonnte payload. All values are strings per docs.fonnte.com:
    target, countryCode, delay must be strings or Fonnte returns status:false.
    """
    settings = get_settings()
    normalized = normalize_phone(target)
    message = _build_link_message(contract_id, result_url, filename)
    return {
        "target": normalized,
        "message": message,
        "countryCode": settings.fonnte_country_code,
        "delay": settings.fonnte_delay,
        "typing": False,
    }


def _deliver(payload: dict) -> dict:
    """
    Blocking HTTP call to Fonnte. Runs in a thread; raises on transport failure,
    returns the parsed JSON response otherwise.
    """
    settings = get_settings()
    token = settings.fonnte_token.strip()
    base_url = settings.fonnte_base_url.rstrip("/")
    url = f"{base_url}/send"
    timeout = settings.fonnte_timeout_seconds

    headers = {"Authorization": token}
    # Fonnte accepts form-encoded; httpx will encode dict as form when using data=
    response = httpx.post(url, headers=headers, data=payload, timeout=timeout)
    # Try to parse JSON regardless of status code — Fonnte returns JSON on errors too
    try:
        data = response.json()
    except Exception:
        data = {"status": False, "reason": f"HTTP {response.status_code}"}
    data["_http_status"] = response.status_code
    return data


async def send_link_notification(
    contract_id: str,
    recipient_phone: str | None,
    result_url: str,
    filename: str = "",
) -> dict:
    """
    Send the result link via WhatsApp/Fonnte, or log the intent when not configured.

    Returns a record dict whose `status` is one of `stub`, `sent`, or `failed`.
    Never raises: the caller persists this outcome before choosing any HTTP status.
    """
    from datetime import UTC, datetime

    timestamp = datetime.now(UTC).isoformat()
    record: dict = {
        "status": STATUS_STUB,
        "contract_id": contract_id,
        "recipient_phone": recipient_phone,
        "result_url": result_url,
        "sent_at": timestamp,
        "note": "",
    }

    if not fonnte_configured():
        masked = _mask_phone(recipient_phone)
        logger.info(
            f"[Whatsapp] Fonnte not configured; logging only. Contract {contract_id} "
            f"-> recipient={masked}, url={result_url}"
        )
        record["note"] = "WhatsApp stub — log only. Set FONNTE_TOKEN to enable sending."
        return record

    if not valid_phone(recipient_phone):
        logger.info(f"[Whatsapp] Refusing to send to invalid phone {recipient_phone!r}.")
        record["status"] = STATUS_FAILED
        record["error"] = "Nomor WhatsApp tidak valid."
        record["note"] = record["error"]
        return record

    phone = recipient_phone.strip() if recipient_phone else ""
    payload = build_fonnte_payload(contract_id, phone, result_url, filename)

    try:
        # httpx is blocking when using httpx.post; offload to thread
        data = await asyncio.to_thread(_deliver, payload)
    except Exception as e:
        logger.error(f"[Whatsapp] Delivery failed for contract {contract_id}: {e!r}")
        record["status"] = STATUS_FAILED
        record["error"] = "Pengiriman WhatsApp gagal. Periksa konfigurasi Fonnte."
        record["note"] = record["error"]
        return record

    # Fonnte returns status:true on success, status:false on failure
    # Note: casing varies (status vs Status), check both
    ok = data.get("status") is True or data.get("Status") is True
    if not ok:
        reason = data.get("reason") or data.get("detail") or "unknown"
        logger.warning(f"[Whatsapp] Fonnte rejected message for {contract_id}: {reason}")
        record["status"] = STATUS_FAILED
        # Do not leak token or raw reason that may contain internal details
        record["error"] = "Pengiriman WhatsApp gagal. Periksa konfigurasi Fonnte."
        record["note"] = record["error"]
        return record

    # Success — capture provider ids if present
    ids = data.get("id") or []
    record["status"] = STATUS_SENT
    record["provider_message_id"] = ids[0] if ids else None
    record["provider_request_id"] = str(data.get("requestid") or "")
    record["note"] = "WhatsApp terkirim."
    masked = _mask_phone(recipient_phone)
    logger.info(f"[Whatsapp] Sent link for contract {contract_id} to {masked}.")
    return record


def _mask_phone(phone: str | None) -> str:
    """Mask phone for logging: 628123456789 -> ***6789"""
    if not phone:
        return "—"
    s = normalize_phone(phone)
    if len(s) <= 4:
        return "***"
    return "***" + s[-4:]
