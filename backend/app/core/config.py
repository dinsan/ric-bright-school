from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings, loaded from env vars (prefix ``LUMINA_``) or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="LUMINA_", extra="ignore")

    app_name: str = "Lumina API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    database_url: str = "postgresql+asyncpg://lumina:lumina@localhost:5432/lumina"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
