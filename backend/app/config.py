from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://legalshield:legalshield@postgres:5432/legalshield"
    redis_url: str = "redis://redis:6379/0"
    hermes_base_url: str = "https://hermes-agent.nousresearch.com"
    hermes_api_key: str = "changeme"
    llm_model: str = "NousResearch/Hermes-3-Llama-3.1-70B"
    default_locale: str = "id-ID"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
