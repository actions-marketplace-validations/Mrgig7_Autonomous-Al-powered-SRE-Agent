from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_name: str = "Task Manager Platform"
    environment: str = "dev"
    api_prefix: str = "/api/v1"

    database_url: str = Field(
        default="sqlite+aiosqlite:///./task_manager.db",
        alias="DATABASE_URL",
    )
    cors_origins: list[str] = ["*"]
    secret_key: str = Field(default="dev-secret-key", alias="SECRET_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
