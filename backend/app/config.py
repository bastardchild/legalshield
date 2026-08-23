from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
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

    # A contract sits in `processing` only while a job holds it. RQ kills jobs at
    # job_timeout (600s), so anything older than this has lost its worker — a crashed
    # container, an OOM kill, or a flushed Redis — and must not stay there forever.
    stuck_contract_timeout_seconds: int = 900
    # How often the reaper sweeps. Set to 0 to disable it (e.g. in tests).
    reaper_interval_seconds: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
