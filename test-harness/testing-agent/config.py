from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    sre_base_url: str = Field(default="http://localhost:8000", alias="SRE_BASE_URL")
    sre_auth_email: str = Field(default="operator@example.com", alias="SRE_AUTH_EMAIL")
    sre_auth_password: str = Field(default="password123", alias="SRE_AUTH_PASSWORD")

    github_api_base_url: str = Field(default="https://api.github.com", alias="GITHUB_API_BASE_URL")
    github_owner: str = Field(default="", alias="GITHUB_OWNER")
    github_repo: str = Field(default="", alias="GITHUB_REPO")
    github_token: str = Field(default="", alias="GITHUB_TOKEN")

    poll_interval_seconds: float = Field(default=5.0, alias="POLL_INTERVAL_SECONDS")
    pipeline_wait_timeout_seconds: int = Field(default=900, alias="PIPELINE_WAIT_TIMEOUT_SECONDS")
    sse_wait_timeout_seconds: int = Field(default=60, alias="SSE_WAIT_TIMEOUT_SECONDS")


def load_settings() -> HarnessSettings:
    return HarnessSettings()
