"""Application settings, read from environment variables or a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./reconciler.db"
    vat_rate: float = 0.07
    local_timezone: str = "Asia/Bangkok"
    api_base_url: str = "http://127.0.0.1:8000"
    default_merchant_id: str = "00000000-0000-0000-0000-000000000001"
    auto_migrate: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
