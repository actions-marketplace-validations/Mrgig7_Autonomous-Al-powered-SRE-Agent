import os
from dataclasses import dataclass


@dataclass
class RuntimeConfig:
    service_name: str
    environment: str
    api_base_url: str


def load_config() -> RuntimeConfig:
    return RuntimeConfig(
        service_name=os.getenv("SERVICE_NAME", "task-manager-platform"),
        environment=os.getenv("ENVIRONMENT", "dev"),
        api_base_url=os.getenv("API_BASE_URL", "http://localhost:8001/api/v1"),
    )
