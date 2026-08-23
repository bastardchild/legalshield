"""
Access-control tests (KNOWN_ISSUES #5).

Cookie signing, the shared access gate, the rate limiter, and the ownership scoping that
replaced "anyone with the UUID can read it". The middleware tests mount a throwaway app
rather than `app.main:app`, so they exercise the middleware chain without needing Postgres.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config import get_settings
from app.middleware import (
    AccessGateMiddleware,
    OwnerMiddleware,
    RateLimitMiddleware,
    client_key,
    is_public,
)
from app.security import new_owner_id, sign, token_matches, unsign
from app.services import rate_limit


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """
    Every test starts from a known configuration.

    `get_settings` is lru_cached, so the cache must be cleared on both sides of the test or
    one test's ACCESS_TOKEN leaks into the next.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    get_settings.cache_clear()
    rate_limit.reset()
    yield
    get_settings.cache_clear()
    rate_limit.reset()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Force the limiter onto its in-memory fallback; the unit suite has no Redis."""
    monkeypatch.setattr(rate_limit, "_hit_redis", lambda *a, **k: None)


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(request: Request):
        return {"owner_id": request.state.owner_id}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(OwnerMiddleware)
    app.add_middleware(AccessGateMiddleware)
    app.add_middleware(RateLimitMiddleware)
    return app


class TestCookieSigning:
    def test_roundtrip(self):
        owner = new_owner_id()
        assert unsign(sign(owner)) == owner

    def test_rejects_tampered_value(self):
        owner = new_owner_id()
        signed = sign(owner)
        forged = new_owner_id() + signed[len(owner):]
        assert unsign(forged) is None

    def test_rejects_tampered_signature(self):
        signed = sign(new_owner_id())
        assert unsign(signed[:-1] + ("0" if signed[-1] != "0" else "1")) is None

    @pytest.mark.parametrize("bad", [None, "", "no-dot", ".", "abc.def", "short.deadbeef"])
    def test_rejects_malformed(self, bad):
        assert unsign(bad) is None

    def test_signature_depends_on_the_secret(self, monkeypatch):
        signed = sign(new_owner_id())
        monkeypatch.setenv("SECRET_KEY", "a-completely-different-key")
        get_settings.cache_clear()
        assert unsign(signed) is None, "a rotated secret must invalidate old cookies"

    def test_owner_ids_are_unique(self):
        assert len({new_owner_id() for _ in range(200)}) == 200


class TestAccessToken:
    def test_open_when_unset(self):
        assert token_matches(None) is True
        assert token_matches("anything") is True

    def test_matches_exactly(self, monkeypatch):
        monkeypatch.setenv("ACCESS_TOKEN", "s3cret")
        get_settings.cache_clear()
        assert token_matches("s3cret") is True
        assert token_matches("s3cre") is False
        assert token_matches("S3CRET") is False
        assert token_matches(None) is False


class TestPublicPaths:
    @pytest.mark.parametrize("path", ["/health", "/health/ready", "/static/app.css"])
    def test_probes_and_assets_are_public(self, path):
        assert is_public(path) is True

    @pytest.mark.parametrize("path", ["/", "/api/contracts/upload", "/contracts/abc"])
    def test_everything_else_is_gated(self, path):
        assert is_public(path) is False


class TestOwnerMiddleware:
    def test_issues_a_cookie_on_first_visit(self):
        with TestClient(build_app()) as client:
            r = client.get("/whoami")
            assert r.status_code == 200
            assert get_settings().owner_cookie_name in r.cookies

    def test_owner_id_is_stable_across_requests(self):
        with TestClient(build_app()) as client:
            first = client.get("/whoami").json()["owner_id"]
            second = client.get("/whoami").json()["owner_id"]
            assert first == second

    def test_separate_clients_get_separate_owners(self):
        app = build_app()
        with TestClient(app) as a, TestClient(app) as b:
            assert a.get("/whoami").json()["owner_id"] != b.get("/whoami").json()["owner_id"]

    def test_forged_cookie_is_replaced_not_trusted(self):
        with TestClient(build_app()) as client:
            client.cookies.set(get_settings().owner_cookie_name, "deadbeef" * 4 + ".bogus")
            body = client.get("/whoami").json()
            assert body["owner_id"] != "deadbeef" * 4

    def test_cookie_is_httponly(self):
        with TestClient(build_app()) as client:
            r = client.get("/whoami")
            assert "httponly" in r.headers["set-cookie"].lower()


class TestAccessGateMiddleware:
    def test_open_when_no_token_configured(self):
        with TestClient(build_app()) as client:
            assert client.get("/whoami").status_code == 200

    def test_rejects_without_token(self, monkeypatch):
        monkeypatch.setenv("ACCESS_TOKEN", "s3cret")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            assert client.get("/whoami").status_code == 401

    def test_accepts_header(self, monkeypatch):
        monkeypatch.setenv("ACCESS_TOKEN", "s3cret")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            r = client.get("/whoami", headers={"X-Access-Token": "s3cret"})
            assert r.status_code == 200

    def test_query_param_is_exchanged_for_a_cookie(self, monkeypatch):
        """A shared link must work once and then keep working without the secret in the URL."""
        monkeypatch.setenv("ACCESS_TOKEN", "s3cret")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            assert client.get("/whoami?token=s3cret").status_code == 200
            assert client.get("/whoami").status_code == 200

    def test_health_stays_reachable(self, monkeypatch):
        """Gating liveness turns a credential mistake into a restart loop."""
        monkeypatch.setenv("ACCESS_TOKEN", "s3cret")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            assert client.get("/health").status_code == 200


class TestRateLimitMiddleware:
    def test_allows_up_to_the_limit(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "3")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            for _ in range(3):
                assert client.get("/whoami").status_code == 200
            assert client.get("/whoami").status_code == 429

    def test_sends_retry_after(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            client.get("/whoami")
            r = client.get("/whoami")
            assert r.status_code == 429
            assert int(r.headers["Retry-After"]) >= 1

    def test_health_is_never_limited(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            for _ in range(5):
                assert client.get("/health").status_code == 200

    def test_disabled_when_zero(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "0")
        get_settings.cache_clear()
        with TestClient(build_app()) as client:
            for _ in range(20):
                assert client.get("/whoami").status_code == 200


class TestClientKey:
    def _request(self, headers: dict, host: str = "1.2.3.4") -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 1234),
            "query_string": b"",
        }
        return Request(scope)

    def test_uses_peer_address_by_default(self):
        """Trusting XFF by default lets any caller mint a fresh identity per request."""
        assert client_key(self._request({"X-Forwarded-For": "9.9.9.9"})) == "1.2.3.4"

    def test_honours_forwarded_for_when_trusted(self, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        get_settings.cache_clear()
        req = self._request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
        assert client_key(req) == "9.9.9.9"


class TestRateLimitWindow:
    def test_counts_within_a_window(self):
        for expected in (1, 2, 3):
            assert rate_limit.check("k", 5, 60, now=1000.0) == expected

    def test_raises_past_the_limit(self):
        rate_limit.check("k", 1, 60, now=1000.0)
        with pytest.raises(rate_limit.RateLimitExceeded):
            rate_limit.check("k", 1, 60, now=1000.0)

    def test_window_rolls_over(self):
        rate_limit.check("k", 1, 60, now=1000.0)
        # 1060 lands in the next 60s bucket, so the counter restarts.
        assert rate_limit.check("k", 1, 60, now=1060.0) == 1

    def test_keys_are_independent(self):
        rate_limit.check("a", 1, 60, now=1000.0)
        assert rate_limit.check("b", 1, 60, now=1000.0) == 1

    def test_zero_limit_disables(self):
        for _ in range(50):
            assert rate_limit.check("k", 0, 60, now=1000.0) == 0

    def test_retry_after_is_bounded_by_the_window(self):
        rate_limit.check("k", 1, 60, now=1000.0)
        with pytest.raises(rate_limit.RateLimitExceeded) as exc:
            rate_limit.check("k", 1, 60, now=1000.0)
        assert 1 <= exc.value.retry_after <= 61

    def test_redis_failure_falls_back_instead_of_refusing(self, monkeypatch):
        """A Redis blip must not become an outage; the limiter degrades to per-process."""
        monkeypatch.setattr(rate_limit, "_hit_redis", lambda *a, **k: None)
        assert rate_limit.check("k", 2, 60, now=1000.0) == 1


class TestOwnershipWiring:
    """Static wiring checks: every contract read must go through the owner filter."""

    def _source(self, name: str) -> str:
        from pathlib import Path

        return Path(f"app/api/{name}").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "name", ["routes_analysis.py", "routes_pages.py", "routes_negotiate.py"]
    )
    def test_routes_use_the_shared_dependency(self, name):
        assert "load_owned_contract" in self._source(name)

    @pytest.mark.parametrize(
        "name", ["routes_analysis.py", "routes_negotiate.py"]
    )
    def test_routes_do_not_select_contracts_unscoped(self, name):
        """An ad-hoc `select(Contract)` would bypass the owner filter entirely."""
        assert "select(Contract)" not in self._source(name)

    def test_upload_stamps_the_owner(self):
        assert "owner_id=current_owner(request)" in self._source("routes_upload.py")

    def test_owner_column_is_required(self):
        from app.db.models import Contract

        assert Contract.__table__.c.owner_id.nullable is False

    def test_missing_contract_and_foreign_contract_are_indistinguishable(self):
        """A 403 would confirm the UUID exists, which is what enumeration is after."""
        src = self._source("deps.py")
        assert "status_code=404" in src
        assert "status_code=403" not in src

    def test_owner_index_is_migrated(self):
        from pathlib import Path

        migrations = "\n".join(
            p.read_text(encoding="utf-8") for p in Path("alembic/versions").glob("*.py")
        )
        assert "ix_contracts_owner_id" in migrations
        assert "owner_id" in migrations
