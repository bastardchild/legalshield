"""
Mailer tests.

`services/mailer.py` was a logging stub. It now sends over SMTP when configured, and stub
mode remains the default so the MVP demo is unaffected. The properties that matter:

* failures are **returned**, never raised — the route records a `negotiation_sends` row from
  this outcome before choosing an HTTP status, so a raised exception would lose the attempt;
* the recipient address is validated before it reaches a header, because a CR/LF in it would
  let a caller inject headers of their own;
* the blocking `smtplib` call runs off the event loop;
* SMTP error text is logged but not returned, since it names the host and the auth user.
"""
import smtplib

import pytest

from app.config import get_settings
from app.services import mailer
from app.services.mailer import (
    STATUS_FAILED,
    STATUS_SENT,
    STATUS_STUB,
    body_text,
    build_message,
    send_counter_draft,
    smtp_configured,
    valid_email,
)

DRAFT = "PASAL 1 — LINGKUP PEKERJAAN\nDirevisi."


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "bot@example.test")
    get_settings.cache_clear()


class _FakeSMTP:
    """Records the conversation so the ordering assertions have something to inspect."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent = []
        self.logins = []
        self.fail_on: str | None = None
        _FakeSMTP.instances.append(self)

    def _record(self, name):
        self.calls.append(name)
        if self.fail_on == name:
            raise smtplib.SMTPException(f"{name} failed on smtp.example.test as bot@example.test")

    def ehlo(self):
        self._record("ehlo")

    def starttls(self):
        self._record("starttls")

    def login(self, user, password):
        self._record("login")
        self.logins.append((user, password))

    def send_message(self, message):
        self._record("send_message")
        self.sent.append(message)

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


class TestEmailValidation:
    @pytest.mark.parametrize(
        "address",
        [
            "klien@perusahaan.com",
            "a.b+tag@sub.domain.co.id",
            "  spaced@example.com  ",
            "UPPER@Example.COM",
        ],
    )
    def test_accepts_reasonable_addresses(self, address):
        assert valid_email(address) is True

    @pytest.mark.parametrize(
        "address",
        [None, "", "   ", "no-at-sign", "@nodomain.com", "user@", "user@nodot", "a b@c.com"],
    )
    def test_rejects_malformed(self, address):
        assert valid_email(address) is False

    @pytest.mark.parametrize(
        "address",
        [
            "victim@example.com\nBcc: attacker@evil.test",
            "victim@example.com\r\nSubject: forged",
            "victim@example.com\rX-Header: x",
        ],
    )
    def test_rejects_header_injection(self, address):
        """A newline in a header value lets the caller append headers of their own."""
        assert valid_email(address) is False

    def test_rejects_absurdly_long_addresses(self):
        assert valid_email("a" * 250 + "@example.com") is False

    @pytest.mark.parametrize("address", ["a@b.com,c@d.com", "a@b.com;c@d.com", "<a@b.com>"])
    def test_rejects_multiple_or_bracketed_recipients(self, address):
        assert valid_email(address) is False


class TestStubMode:
    async def test_stub_when_smtp_is_unset(self):
        assert smtp_configured() is False
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert result["status"] == STATUS_STUB

    async def test_stub_does_not_touch_smtp(self, fake_smtp):
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert fake_smtp.instances == []

    async def test_stub_tolerates_a_missing_recipient(self):
        """The email field is optional in the UI; the record is still written."""
        result = await send_counter_draft("c-1", None, DRAFT)
        assert result["status"] == STATUS_STUB

    async def test_stub_note_explains_how_to_enable_sending(self):
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert "SMTP_HOST" in result["note"]

    async def test_whitespace_host_is_still_stub(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "   ")
        get_settings.cache_clear()
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert result["status"] == STATUS_STUB


class TestSending:
    async def test_sends_and_reports_sent(self, smtp_env, fake_smtp):
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert result["status"] == STATUS_SENT
        assert len(fake_smtp.instances[0].sent) == 1

    async def test_uses_the_configured_host_port_and_timeout(self, smtp_env, fake_smtp):
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        client = fake_smtp.instances[0]
        assert (client.host, client.port) == ("smtp.example.test", 587)
        assert client.timeout == get_settings().smtp_timeout_seconds

    async def test_starttls_precedes_login(self, smtp_env, fake_smtp):
        """Credentials must not cross the wire before the session is encrypted."""
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        calls = fake_smtp.instances[0].calls
        assert calls.index("starttls") < calls.index("login")

    async def test_ehlo_is_repeated_after_starttls(self, smtp_env, fake_smtp):
        """The server's AUTH capability is only valid for the encrypted session."""
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        calls = fake_smtp.instances[0].calls
        assert calls.count("ehlo") == 2
        starttls_at = calls.index("starttls")
        assert calls.index("ehlo", starttls_at) > starttls_at

    async def test_ssl_mode_skips_starttls(self, smtp_env, fake_smtp, monkeypatch):
        monkeypatch.setenv("SMTP_USE_SSL", "true")
        get_settings.cache_clear()
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert "starttls" not in fake_smtp.instances[0].calls

    async def test_login_is_skipped_without_a_username(self, smtp_env, fake_smtp, monkeypatch):
        """Some relays authenticate by IP; sending an empty AUTH would be rejected."""
        monkeypatch.setenv("SMTP_USERNAME", "")
        get_settings.cache_clear()
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert "login" not in fake_smtp.instances[0].calls

    async def test_connection_is_always_closed(self, smtp_env, fake_smtp):
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert fake_smtp.instances[0].calls[-1] in ("quit", "close")

    async def test_runs_off_the_event_loop(self, smtp_env, fake_smtp, monkeypatch):
        """smtplib blocks; running it inline would stall every other request."""
        used = {"thread": False}
        real = mailer.asyncio.to_thread

        async def spy(fn, *args, **kwargs):
            used["thread"] = True
            return await real(fn, *args, **kwargs)

        monkeypatch.setattr(mailer.asyncio, "to_thread", spy)
        await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert used["thread"] is True


class TestSendFailures:
    async def test_invalid_recipient_fails_without_connecting(self, smtp_env, fake_smtp):
        result = await send_counter_draft("c-1", "not-an-email", DRAFT)
        assert result["status"] == STATUS_FAILED
        assert fake_smtp.instances == []

    async def test_missing_recipient_fails_when_smtp_is_configured(self, smtp_env, fake_smtp):
        """With SMTP on there is nowhere to send; stub mode is no longer the answer."""
        result = await send_counter_draft("c-1", None, DRAFT)
        assert result["status"] == STATUS_FAILED

    @pytest.mark.parametrize("stage", ["ehlo", "starttls", "login", "send_message"])
    async def test_smtp_errors_are_returned_not_raised(
        self, smtp_env, fake_smtp, monkeypatch, stage
    ):
        original_init = _FakeSMTP.__init__

        def failing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.fail_on = stage

        monkeypatch.setattr(_FakeSMTP, "__init__", failing_init)
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert result["status"] == STATUS_FAILED
        assert result["error"]

    async def test_connection_refused_is_returned_not_raised(self, smtp_env, monkeypatch):
        def refuse(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert result["status"] == STATUS_FAILED

    async def test_error_text_does_not_leak_the_host_or_credentials(
        self, smtp_env, fake_smtp, monkeypatch
    ):
        original_init = _FakeSMTP.__init__

        def failing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.fail_on = "login"

        monkeypatch.setattr(_FakeSMTP, "__init__", failing_init)
        result = await send_counter_draft("c-1", "klien@example.com", DRAFT)
        assert "smtp.example.test" not in result["error"]
        assert "bot@example.test" not in result["error"]


class TestMessageConstruction:
    def test_headers_and_body(self, smtp_env):
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert message["To"] == "klien@example.com"
        assert "bot@example.test" in message["From"]
        assert message["Subject"]
        assert DRAFT in body_text(message)

    def test_contract_id_is_included_for_traceability(self, smtp_env):
        message = build_message("contract-42", "klien@example.com", DRAFT)
        assert "contract-42" in body_text(message)

    def test_carries_the_not_legal_advice_disclaimer(self, smtp_env):
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert "bukan nasihat hukum" in body_text(message).lower()

    def test_empty_draft_still_produces_a_body(self, smtp_env):
        message = build_message("c-1", "klien@example.com", "")
        assert body_text(message).strip()

    def test_header_injection_is_refused_at_the_header_layer(self, smtp_env):
        """Defence in depth: valid_email already rejects this, EmailMessage also would."""
        import email.errors

        with pytest.raises((ValueError, email.errors.HeaderParseError)):
            build_message("c-1", "victim@example.com\nBcc: attacker@evil.test", DRAFT)


class TestAttachment:
    """The draft is also attached: 20 KB of contract text is edited, not read inline."""

    def _attachments(self, message):
        return list(message.iter_attachments())

    def test_exactly_one_attachment(self, smtp_env):
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert len(self._attachments(message)) == 1

    def test_attachment_carries_the_draft(self, smtp_env):
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert DRAFT in self._attachments(message)[0].get_content()

    def test_filename_names_the_contract(self, smtp_env):
        message = build_message("contract-42", "klien@example.com", DRAFT)
        assert self._attachments(message)[0].get_filename() == "draft-kontrak-contract-42.txt"

    def test_attachment_is_plain_text(self, smtp_env):
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert self._attachments(message)[0].get_content_type() == "text/plain"

    def test_non_ascii_survives_the_round_trip(self, smtp_env):
        """Indonesian drafts use typographic dashes; a latin-1 default would mangle them."""
        draft = "PASAL 1 — Ketentuan Pembayaran ditinjau ulang."
        message = build_message("c-1", "klien@example.com", draft)
        assert draft in self._attachments(message)[0].get_content()

    def test_message_is_multipart(self, smtp_env):
        """Guards the get_body() accessor: a regression to set_content alone breaks callers."""
        message = build_message("c-1", "klien@example.com", DRAFT)
        assert message.is_multipart()


class TestRouteWiring:
    """The send route must record the attempt before reporting a failure."""

    def _source(self) -> str:
        from pathlib import Path

        return Path("app/api/routes_negotiate.py").read_text(encoding="utf-8")

    def test_awaits_the_mailer(self):
        assert "await send_counter_draft" in self._source()

    def test_persists_the_real_outcome_status(self):
        assert 'status=outcome["status"]' in self._source()

    def test_records_before_raising(self):
        src = self._source()
        assert src.index("db.add(send_record)") < src.index("status_code=502")

    def test_reports_a_delivery_failure_as_502(self):
        assert "502" in self._source()
