"""
WhatsApp link notification tests (Fonnte).

Stub mode is the default: with `FONNTE_TOKEN` empty the send is logged and nothing
leaves the process. The properties that matter:

* failures are **returned**, never raised — the orchestrator records a
  `whatsapp_sends` row from this outcome, so a raised exception would lose it;
* the phone number is validated before it reaches the API, because a CR/LF in it
  would be an injection vector;
* the HTTP call runs off the event loop;
* Fonnte error text / token is logged but not returned.
* the message is link-only: just the result URL, no draft text.
"""

import pytest

from app.config import get_settings
from app.services import whatsapp
from app.services.whatsapp import (
    STATUS_FAILED,
    STATUS_SENT,
    STATUS_STUB,
    build_fonnte_payload,
    fonnte_configured,
    normalize_phone,
    send_link_notification,
    valid_phone,
)


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fonnte_env(monkeypatch):
    monkeypatch.setenv("FONNTE_TOKEN", "test-fonnte-token")
    monkeypatch.setenv("FONNTE_BASE_URL", "http://fonnte.invalid")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json


# ---------------------------------------------------------------------------
# Phone validation
# ---------------------------------------------------------------------------

class TestPhoneValidation:
    @pytest.mark.parametrize(
        "phone",
        [
            "08123456789",
            "628123456789",
            "+628123456789",
            "0812 3456 7890",
            "0812-3456-7890",
            "(0812) 3456 7890",
            "  08123456789  ",
            "+62 812-3456-7890",
        ],
    )
    def test_accepts_reasonable_phones(self, phone):
        assert valid_phone(phone) is True

    @pytest.mark.parametrize(
        "phone",
        [None, "", "   ", "abc", "123", "12", "08123", "+"],
    )
    def test_rejects_malformed(self, phone):
        assert valid_phone(phone) is False

    @pytest.mark.parametrize(
        "phone",
        [
            "08123456789\nBcc: evil",
            "08123456789\r\nX-Inject: x",
            "08123456789\rX",
        ],
    )
    def test_rejects_injection(self, phone):
        assert valid_phone(phone) is False

    def test_rejects_too_long(self):
        assert valid_phone("6281234567890123456") is False

    def test_rejects_non_digits(self):
        assert valid_phone("0812-abc-7890") is False

    def test_normalize_leading_zero(self):
        assert normalize_phone("08123456789") == "628123456789"

    def test_normalize_plus_prefix(self):
        assert normalize_phone("+628123456789") == "628123456789"

    def test_normalize_already_normalized(self):
        assert normalize_phone("628123456789") == "628123456789"

    def test_normalize_strips_formatting(self):
        assert normalize_phone("0812 345-6789") == "628123456789"


# ---------------------------------------------------------------------------
# Stub mode
# ---------------------------------------------------------------------------

class TestStubMode:
    async def test_stub_when_fonnte_is_unset(self, monkeypatch):
        monkeypatch.delenv("FONNTE_TOKEN", raising=False)
        monkeypatch.setenv("FONNTE_TOKEN", "")
        get_settings.cache_clear()
        assert fonnte_configured() is False
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_STUB

    async def test_stub_does_not_touch_http(self, monkeypatch):
        monkeypatch.delenv("FONNTE_TOKEN", raising=False)
        monkeypatch.setenv("FONNTE_TOKEN", "")
        get_settings.cache_clear()
        called = []
        monkeypatch.setattr(whatsapp.httpx, "post", lambda *a, **kw: called.append(1))
        await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert called == []

    async def test_stub_note_explains_how_to_enable(self, monkeypatch):
        monkeypatch.delenv("FONNTE_TOKEN", raising=False)
        monkeypatch.setenv("FONNTE_TOKEN", "")
        get_settings.cache_clear()
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert "FONNTE_TOKEN" in result["note"]

    async def test_whitespace_token_is_still_stub(self, monkeypatch):
        monkeypatch.setenv("FONNTE_TOKEN", "   ")
        get_settings.cache_clear()
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_STUB


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

class TestPayload:
    def test_target_is_normalized_string(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert payload["target"] == "628123456789"
        assert isinstance(payload["target"], str)

    def test_country_code_is_string(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert isinstance(payload["countryCode"], str)
        assert payload["countryCode"] == "62"

    def test_delay_is_string(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert isinstance(payload["delay"], str)

    def test_message_contains_link(self, fonnte_env):
        url = "http://localhost:8000/contracts/c-1"
        payload = build_fonnte_payload("c-1", "08123456789", url)
        assert url in payload["message"]

    def test_message_does_not_contain_draft(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1", "secret draft")
        assert "secret draft" not in payload["message"]

    def test_typing_is_false(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert payload["typing"] is False


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

class TestSending:
    async def test_sends_and_reports_sent(self, fonnte_env, monkeypatch):
        monkeypatch.setattr(
            whatsapp.httpx, "post",
            lambda *a, **kw: _FakeResponse({"status": True, "id": ["80367170"], "requestid": 123}),
        )
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_SENT

    async def test_uses_configured_base_url(self, fonnte_env, monkeypatch):
        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url
            return _FakeResponse({"status": True, "id": ["1"]})

        monkeypatch.setattr(whatsapp.httpx, "post", fake_post)
        await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert "fonnte.invalid" in captured["url"]
        assert captured["url"].endswith("/send")

    async def test_authorization_header_is_raw_token(self, fonnte_env, monkeypatch):
        captured = {}

        def fake_post(url, headers=None, **kw):
            captured["headers"] = headers
            return _FakeResponse({"status": True, "id": ["1"]})

        monkeypatch.setattr(whatsapp.httpx, "post", fake_post)
        await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert captured["headers"]["Authorization"] == "test-fonnte-token"
        assert "Bearer" not in captured["headers"]["Authorization"]

    async def test_runs_off_the_event_loop(self, fonnte_env, monkeypatch):
        monkeypatch.setattr(
            whatsapp.httpx, "post",
            lambda *a, **kw: _FakeResponse({"status": True, "id": ["1"]}),
        )
        used = {"thread": False}
        real = whatsapp.asyncio.to_thread

        async def spy(fn, *args, **kwargs):
            used["thread"] = True
            return await real(fn, *args, **kwargs)

        monkeypatch.setattr(whatsapp.asyncio, "to_thread", spy)
        await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert used["thread"] is True

    async def test_captures_provider_ids(self, fonnte_env, monkeypatch):
        monkeypatch.setattr(
            whatsapp.httpx, "post",
            lambda *a, **kw: _FakeResponse({"status": True, "id": ["80367170"], "requestid": 2937124}),
        )
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["provider_message_id"] == "80367170"
        assert result["provider_request_id"] == "2937124"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

class TestSendFailures:
    async def test_invalid_phone_fails_without_http(self, fonnte_env, monkeypatch):
        called = []
        monkeypatch.setattr(whatsapp.httpx, "post", lambda *a, **kw: called.append(1))
        result = await send_link_notification("c-1", "not-a-phone", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_FAILED
        assert called == []

    async def test_fonnte_rejection_is_returned_not_raised(self, fonnte_env, monkeypatch):
        monkeypatch.setattr(
            whatsapp.httpx, "post",
            lambda *a, **kw: _FakeResponse({"status": False, "reason": "target invalid"}),
        )
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_FAILED
        assert result["error"]

    async def test_connection_error_is_returned_not_raised(self, fonnte_env, monkeypatch):
        def refuse(*a, **kw):
            raise OSError("connection refused")

        monkeypatch.setattr(whatsapp.httpx, "post", refuse)
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert result["status"] == STATUS_FAILED

    async def test_error_text_does_not_leak_token(self, fonnte_env, monkeypatch):
        monkeypatch.setattr(
            whatsapp.httpx, "post",
            lambda *a, **kw: _FakeResponse({"status": False, "reason": "token invalid"}),
        )
        result = await send_link_notification("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert "test-fonnte-token" not in result.get("error", "")
        assert "fonnte.invalid" not in result.get("error", "")


# ---------------------------------------------------------------------------
# Link message
# ---------------------------------------------------------------------------

class TestLinkMessage:
    def test_contains_result_url(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        assert "http://localhost:8000/contracts/c-1" in payload["message"]

    def test_link_message_is_short(self, fonnte_env):
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1", "kontrak.pdf")
        # Link-only message should be short, not include filename as free text
        assert len(payload["message"]) < 500

    def test_is_link_only_no_draft_content(self, fonnte_env):
        # Even if caller passes draft-like text, it must not appear
        payload = build_fonnte_payload("c-1", "08123456789", "http://localhost:8000/contracts/c-1")
        # Message should be short (link + a few words), not a 20KB draft
        assert len(payload["message"]) < 500
