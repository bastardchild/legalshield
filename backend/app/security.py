"""
Access control primitives (KNOWN_ISSUES #5).

The app previously had no authentication, no authorization, no rate limiting and no CORS
policy: anyone who could reach port 8000 could upload contracts, read anyone else's
contract by guessing or leaking a UUID, and spend the operator's LLM budget.

Three independent layers, each of which can be switched off:

1. **Owner identity** — every visitor is issued a signed, anonymous owner id in a cookie.
   Contracts are stamped with it and every read is scoped to it. This closes the "read
   any contract by UUID" hole without inventing a login flow the product has not
   specified. It is *not* an account system: clearing cookies loses access.
2. **Shared access token** (`ACCESS_TOKEN`) — an optional gate in front of everything. Set
   it and the whole app requires the token; leave it empty and the app is open, which is
   only appropriate on localhost.
3. **Rate limits** — see `services/rate_limit.py`.

Signing uses HMAC-SHA256 over `SECRET_KEY`. With no `SECRET_KEY` configured a random key
is generated per process, so cookies survive neither a restart nor a second replica; that
is deliberately noisy rather than silently insecure.
"""
import hmac
import logging
import secrets
import uuid
from hashlib import sha256

from app.config import get_settings

logger = logging.getLogger(__name__)

# Owner ids are opaque; 32 hex chars is a uuid4 without dashes.
OWNER_ID_LENGTH = 32
COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90 days

_ephemeral_key: str | None = None


def _signing_key() -> bytes:
    """
    Key material for cookie signatures.

    Falls back to a per-process random key so an unconfigured deployment cannot be
    trivially forged; the cost is that cookies break on restart and across replicas.
    """
    global _ephemeral_key
    configured = get_settings().secret_key.strip()
    if configured:
        return configured.encode()
    if _ephemeral_key is None:
        _ephemeral_key = secrets.token_hex(32)
        logger.warning(
            "SECRET_KEY is not set: using an ephemeral signing key. Owner cookies will be "
            "invalidated on restart and will not validate across replicas."
        )
    return _ephemeral_key.encode()


def _digest(value: str) -> str:
    return hmac.new(_signing_key(), value.encode(), sha256).hexdigest()[:32]


def new_owner_id() -> str:
    return uuid.uuid4().hex


def sign(value: str) -> str:
    """`value.signature`, safe to hand to a client."""
    return f"{value}.{_digest(value)}"


def unsign(signed: str | None) -> str | None:
    """
    Recover a signed value, or None if it is absent, malformed, or tampered with.

    Comparison is constant-time so the signature cannot be recovered byte by byte.
    """
    if not signed or "." not in signed:
        return None
    value, _, signature = signed.rpartition(".")
    if not value or len(value) != OWNER_ID_LENGTH:
        return None
    if not hmac.compare_digest(signature, _digest(value)):
        logger.warning("Rejected a cookie with an invalid signature.")
        return None
    return value


def token_matches(candidate: str | None) -> bool:
    """Constant-time comparison against the configured shared access token."""
    expected = get_settings().access_token
    if not expected:
        return True
    if not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


def access_required() -> bool:
    return bool(get_settings().access_token)
