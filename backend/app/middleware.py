"""
Request-level access control (KNOWN_ISSUES #5).

Three middlewares, applied outermost-first:

1. `RateLimitMiddleware` — a per-IP request cap, so an unauthenticated caller cannot
   hammer the app or the LLM budget.
2. `AccessGateMiddleware` — the optional shared `ACCESS_TOKEN`. Nothing but the health
   probes and static assets is reachable without it once it is configured.
3. `OwnerMiddleware` — issues and validates the signed anonymous owner cookie that scopes
   contract reads to whoever uploaded them.

Ordering matters. Starlette applies `add_middleware` in reverse, so the calls in `main.py`
are written bottom-up: rate limiting must run first (it is the cheapest rejection), the
access gate before identity (no point minting an owner id for a request about to be
refused), and the owner cookie last so it is only issued to admitted requests.

The probes are exempt because an orchestrator cannot present a token or hold a cookie, and
gating liveness behind auth turns a credential mistake into a restart loop.
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.security import COOKIE_MAX_AGE, new_owner_id, sign, token_matches, unsign
from app.services.rate_limit import RateLimitExceeded, check

logger = logging.getLogger(__name__)

# Reachable without a token: probes (an orchestrator has no credentials) and static assets
# (they carry nothing sensitive, and gating them breaks the login page's own styling).
PUBLIC_PATH_PREFIXES = ("/health", "/static")

ACCESS_HEADER = "x-access-token"
ACCESS_QUERY_PARAM = "token"

UNAUTHORIZED_DETAIL = (
    "Akses ditolak. Sertakan token akses pada header X-Access-Token atau parameter ?token=."
)


def is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PATH_PREFIXES)


def client_key(request: Request) -> str:
    """
    Rate-limit identity for a request.

    `X-Forwarded-For` is only honoured when `TRUST_PROXY_HEADERS` is on. Trusting it by
    default would let any caller spoof a fresh identity per request with one header and
    bypass the limiter entirely.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP request cap. Uploads carry an additional, much tighter cap in their route."""

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        limit = settings.rate_limit_requests_per_minute
        if limit <= 0 or is_public(request.url.path):
            return await call_next(request)

        try:
            check(f"req:{client_key(request)}", limit, 60)
        except RateLimitExceeded as e:
            logger.warning(f"[RateLimit] {client_key(request)} exceeded {e.limit}/min")
            return JSONResponse(
                {"detail": "Terlalu banyak permintaan. Coba lagi sebentar."},
                status_code=429,
                headers={"Retry-After": str(e.retry_after)},
            )
        return await call_next(request)


class AccessGateMiddleware(BaseHTTPMiddleware):
    """
    Shared-secret gate. Inactive unless `ACCESS_TOKEN` is set.

    A token supplied as a query parameter is stored in a cookie so a shared link works
    once and then keeps working without the secret staying in the address bar.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.access_token or is_public(request.url.path):
            return await call_next(request)

        from_query = request.query_params.get(ACCESS_QUERY_PARAM)
        candidate = (
            request.headers.get(ACCESS_HEADER)
            or from_query
            or request.cookies.get(settings.access_cookie_name)
        )
        if not token_matches(candidate):
            logger.warning(f"[AccessGate] Rejected {request.method} {request.url.path}")
            return JSONResponse({"detail": UNAUTHORIZED_DETAIL}, status_code=401)

        response = await call_next(request)
        if from_query:
            response.set_cookie(
                settings.access_cookie_name,
                candidate,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
            )
        return response


class OwnerMiddleware(BaseHTTPMiddleware):
    """
    Attach a stable, anonymous owner id to every request as `request.state.owner_id`.

    This is the authorization primitive: contracts are stamped with the id of the owner who
    uploaded them and every read is filtered by it, so knowing a UUID is no longer enough
    to read someone else's contract. It is not an account system — it is the smallest thing
    that closes the hole without committing the product to a login flow.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        owner_id = unsign(request.cookies.get(settings.owner_cookie_name))
        issued = owner_id is None
        if issued:
            owner_id = new_owner_id()
        request.state.owner_id = owner_id

        response = await call_next(request)
        if issued:
            response.set_cookie(
                settings.owner_cookie_name,
                sign(owner_id),
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
            )
        return response


def current_owner(request: Request) -> str:
    """
    Owner id for the request, for use as a FastAPI dependency.

    Falls back to minting one rather than raising: the middleware always runs in the app,
    but a route imported into a test or a script should not blow up on a missing cookie.
    """
    owner_id = getattr(request.state, "owner_id", None)
    return owner_id or new_owner_id()
