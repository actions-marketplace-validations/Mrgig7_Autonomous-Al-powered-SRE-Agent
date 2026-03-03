"""CI Provider abstraction layer.

Provides a unified interface for all CI/CD platform integrations:
- Webhook signature verification
- Event payload parsing
- Log fetching from provider APIs
- Artifact retrieval

Each provider implementation inherits from BaseCIProvider and
registers with the ProviderRegistry.
"""

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional

import httpx

from sre_agent.schemas.normalized import CIProvider, FailureType, NormalizedPipelineEvent

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Supported CI/CD provider types."""

    GITHUB = "github"
    GITLAB = "gitlab"
    CIRCLECI = "circleci"
    JENKINS = "jenkins"
    AZURE_DEVOPS = "azure_devops"


@dataclass
class ProviderConfig:
    """Configuration for a CI provider."""

    provider_type: ProviderType
    api_url: Optional[str] = None
    api_token: Optional[str] = None
    webhook_secret: Optional[str] = None
    username: Optional[str] = None  # For Jenkins
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookVerificationResult:
    """Result of webhook signature verification."""

    valid: bool
    provider: ProviderType
    event_type: str
    delivery_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class FetchedLogs:
    """Logs fetched from a CI provider."""

    job_id: str
    content: str
    truncated: bool = False
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BaseCIProvider(ABC):
    """Abstract base class for CI provider integrations.

    Each provider must implement:
    - verify_webhook(): Validate incoming webhook signatures
    - parse_event(): Extract event data from payload
    - normalize_event(): Convert to NormalizedPipelineEvent
    - fetch_logs(): Get job/build logs from API
    """

    def __init__(self, config: ProviderConfig):
        """Initialize provider with configuration.

        Args:
            config: Provider-specific configuration
        """
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type."""
        ...

    @property
    @abstractmethod
    def ci_provider_enum(self) -> CIProvider:
        """Return the CIProvider enum for normalized events."""
        ...

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for API calls."""
        if self._client is None:
            headers = self._get_auth_headers()
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for API requests."""
        ...

    @abstractmethod
    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookVerificationResult:
        """Verify webhook signature and extract metadata.

        Args:
            headers: Request headers
            body: Raw request body

        Returns:
            Verification result with event metadata
        """
        ...

    @abstractmethod
    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse provider-specific payload into structured data.

        Args:
            payload: Raw webhook payload

        Returns:
            Parsed event data with standardized keys
        """
        ...

    @abstractmethod
    def normalize_event(
        self,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> NormalizedPipelineEvent:
        """Convert payload to NormalizedPipelineEvent.

        Args:
            payload: Raw webhook payload
            correlation_id: Request correlation ID

        Returns:
            Normalized pipeline event
        """
        ...

    @abstractmethod
    async def fetch_logs(
        self,
        job_id: str,
        **kwargs,
    ) -> FetchedLogs:
        """Fetch job/build logs from provider API.

        Args:
            job_id: Provider-specific job identifier
            **kwargs: Additional provider-specific parameters

        Returns:
            Fetched log content
        """
        ...

    def should_process(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Determine if this event should be processed.

        Default implementation checks for failure status.
        Override for provider-specific logic.

        Args:
            payload: Webhook payload

        Returns:
            Tuple of (should_process, reason)
        """
        return True, ""

    def generate_idempotency_key(
        self,
        repo: str,
        pipeline_id: str,
        job_id: str,
        attempt: int = 1,
    ) -> str:
        """Generate idempotency key for deduplication."""
        return f"{self.provider_type.value}:{repo}:{pipeline_id}:{job_id}:{attempt}"

    def infer_failure_type(self, job_name: str, status: str) -> FailureType:
        """Infer failure type from job name and status.

        Uses common patterns across providers.
        """
        import re

        job_lower = job_name.lower()

        # Check for timeout
        if "timeout" in status.lower() or "timed_out" in status.lower():
            return FailureType.TIMEOUT

        # Test patterns
        if re.search(r"\b(test|tests|unit|integration|e2e|spec)\b", job_lower):
            return FailureType.TEST

        # Deploy patterns
        if re.search(r"\b(deploy|release|publish)\b", job_lower):
            return FailureType.DEPLOY

        # Build patterns
        if re.search(r"\b(build|compile|package|bundle)\b", job_lower):
            return FailureType.BUILD

        # Infrastructure patterns
        if re.search(r"\b(infra|terraform|provision|setup)\b", job_lower):
            return FailureType.INFRASTRUCTURE

        # Default to BUILD
        return FailureType.BUILD

    @staticmethod
    def verify_hmac_signature(
        secret: str,
        body: bytes,
        signature: str,
        algorithm: str = "sha256",
        prefix: str = "",
    ) -> bool:
        """Verify HMAC signature.

        Works for GitHub, GitLab, and similar providers.
        """
        if algorithm == "sha256":
            hasher = hashlib.sha256
        elif algorithm == "sha1":
            hasher = hashlib.sha1
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        expected = hmac.new(
            secret.encode(),
            body,
            hasher,
        ).hexdigest()

        # Handle prefixed signatures (e.g., "sha256=...")
        if prefix and signature.startswith(prefix):
            signature = signature[len(prefix) :]

        return hmac.compare_digest(expected, signature)


class ProviderRegistry:
    """Registry for CI provider implementations.

    Allows dynamic registration and lookup of providers.
    """

    _providers: dict[ProviderType, type[BaseCIProvider]] = {}
    _instances: dict[ProviderType, BaseCIProvider] = {}

    @classmethod
    def register(cls, provider_type: ProviderType):
        """Decorator to register a provider class."""

        def decorator(provider_class: type[BaseCIProvider]):
            cls._providers[provider_type] = provider_class
            logger.debug(f"Registered CI provider: {provider_type.value}")
            return provider_class

        return decorator

    @classmethod
    def get_provider_class(cls, provider_type: ProviderType) -> type[BaseCIProvider]:
        """Get provider class by type."""
        if provider_type not in cls._providers:
            raise ValueError(f"Unknown provider type: {provider_type}")
        return cls._providers[provider_type]

    @classmethod
    def get_provider(
        cls,
        provider_type: ProviderType,
        config: Optional[ProviderConfig] = None,
    ) -> BaseCIProvider:
        """Get or create provider instance.

        Args:
            provider_type: Type of provider
            config: Optional configuration (uses defaults if not provided)

        Returns:
            Provider instance
        """
        if provider_type in cls._instances:
            return cls._instances[provider_type]

        provider_class = cls.get_provider_class(provider_type)

        if config is None:
            config = cls._get_default_config(provider_type)

        instance = provider_class(config)
        cls._instances[provider_type] = instance
        return instance

    @classmethod
    def _get_default_config(cls, provider_type: ProviderType) -> ProviderConfig:
        """Get default configuration from settings."""
        from sre_agent.config import get_settings

        settings = get_settings()

        configs = {
            ProviderType.GITHUB: ProviderConfig(
                provider_type=ProviderType.GITHUB,
                api_url="https://api.github.com",
                webhook_secret=getattr(settings, "github_webhook_secret", None),
            ),
            ProviderType.GITLAB: ProviderConfig(
                provider_type=ProviderType.GITLAB,
                api_url=getattr(settings, "gitlab_url", "https://gitlab.com"),
                api_token=getattr(settings, "gitlab_token", None),
                webhook_secret=getattr(settings, "gitlab_webhook_token", None),
            ),
            ProviderType.CIRCLECI: ProviderConfig(
                provider_type=ProviderType.CIRCLECI,
                api_url="https://circleci.com/api/v2",
                api_token=getattr(settings, "circleci_token", None),
                webhook_secret=getattr(settings, "circleci_webhook_secret", None),
            ),
            ProviderType.JENKINS: ProviderConfig(
                provider_type=ProviderType.JENKINS,
                api_url=getattr(settings, "jenkins_url", None),
                api_token=getattr(settings, "jenkins_token", None),
                username=getattr(settings, "jenkins_user", None),
            ),
            ProviderType.AZURE_DEVOPS: ProviderConfig(
                provider_type=ProviderType.AZURE_DEVOPS,
                api_url="https://dev.azure.com",
                api_token=getattr(settings, "azure_devops_pat", None),
                extra={
                    "organization": getattr(settings, "azure_devops_org", None),
                },
            ),
        }

        return configs.get(provider_type, ProviderConfig(provider_type=provider_type))

    @classmethod
    def list_registered(cls) -> list[ProviderType]:
        """List all registered provider types."""
        return list(cls._providers.keys())

    @classmethod
    async def close_all(cls) -> None:
        """Close all provider instances."""
        for instance in cls._instances.values():
            await instance.close()
        cls._instances.clear()
