from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield"
    redis_url: str = "redis://redis:6379/0"
    hermes_base_url: str = "https://hermes-agent.nousresearch.com"
    hermes_api_key: str = "changeme"
    llm_model: str = "NousResearch/Hermes-3-Llama-3.1-70B"
    default_locale: str = "id-ID"

    # Per-request LLM timeout. Must stay well below the RQ job_timeout (600s) so a
    # hanging provider surfaces as a failed agent rather than a killed worker.
    llm_timeout_seconds: float = 120.0
    # Transport-level retries handled by the OpenAI SDK (connection errors, 429, 5xx).
    llm_max_retries: int = 2
    # Additional application-level retries when the model returns unparseable JSON.
    llm_json_retries: int = 2
    # Hard cap on contract characters sent to the model, to stay inside the context
    # window. Roughly 4 chars/token, so 60k chars is ~15k tokens.
    max_contract_chars: int = 60_000
    # Cap on skill-store patterns injected as RAG context, newest-most-matched first.
    max_rag_patterns: int = 40
    # Directory holding the shipped seed data, relative to the working directory.
    seed_dir: str = "seed"
    # Cap on legal references injected into the tax agent's prompt.
    max_legal_references: int = 25

    # --- Upload contract filter (gate before any LLM spend) -------------------------
    # The upload route rejects documents that do not score like a contract/legal paper
    # against the weighted lexicon in seed/legal_lexicon.json. Set false to disable the
    # gate entirely (not recommended — every accepted upload costs three LLM agent calls).
    contract_filter_enabled: bool = True
    # Documents scoring below this are rejected as non-contracts.
    contract_filter_min_score: float = 0.35
    # Below this token count the document is too short to judge fairly.
    contract_filter_min_tokens: int = 60

    # --- Security (KNOWN_ISSUES #5) ------------------------------------------------
    # HMAC key for the owner cookie. Generated per-process when empty, which invalidates
    # cookies on restart and does not validate across replicas — set it in production.
    secret_key: str = ""
    # Shared gate for the whole app. Empty means the app is open, which is only safe on
    # localhost. When set, every request must present it (header, query param, or cookie).
    access_token: str = ""
    # Comma-separated CORS allow-list. Empty means no CORS middleware at all, so browsers
    # refuse cross-origin reads — the correct default for a same-origin app.
    allowed_origins: str = ""
    # Fixed-window rate limits, per client IP. 0 disables the limiter.
    rate_limit_requests_per_minute: int = 240
    # Uploads are the expensive path: each one spends LLM budget. Limited separately.
    rate_limit_uploads_per_hour: int = 20
    owner_cookie_name: str = "ls_owner"
    access_cookie_name: str = "ls_access"
    # Set true when serving over HTTPS so the cookies are not sent in cleartext.
    cookie_secure: bool = False
    # Only enable behind a proxy you control. Otherwise any caller can spoof
    # X-Forwarded-For and get a fresh rate-limit identity on every request.
    trust_proxy_headers: bool = False

    # --- Email delivery -------------------------------------------------------------
    # Empty host keeps services/mailer.py in stub mode (log only), which is what the MVP
    # demo relies on. Set it to send for real.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # STARTTLS on a plaintext port (587). Mutually exclusive with smtp_use_ssl.
    smtp_use_tls: bool = True
    # Implicit TLS from the first byte, usually port 465.
    smtp_use_ssl: bool = False
    smtp_from: str = ""
    smtp_from_name: str = "LegalShield Agent"
    # Must stay well under the request timeout: this send happens inline in a POST handler.
    smtp_timeout_seconds: float = 15.0

    # A contract sits in `processing` only while a job holds it. RQ kills jobs at
    # job_timeout (600s), so anything older than this has lost its worker — a crashed
    # container, an OOM kill, or a flushed Redis — and must not stay there forever.
    stuck_contract_timeout_seconds: int = 900
    # How often the reaper sweeps. Set to 0 to disable it (e.g. in tests).
    reaper_interval_seconds: int = 300

    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
