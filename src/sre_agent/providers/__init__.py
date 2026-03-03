"""CI Provider package.

Unified interface for multiple CI/CD platform integrations.
"""

import importlib

from sre_agent.providers.base_provider import (
    BaseCIProvider,
    FetchedLogs,
    ProviderConfig,
    ProviderRegistry,
    ProviderType,
    WebhookVerificationResult,
)

__all__ = [
    "BaseCIProvider",
    "FetchedLogs",
    "ProviderConfig",
    "ProviderRegistry",
    "ProviderType",
    "WebhookVerificationResult",
]


# Import providers to trigger registration
# These imports must come after the base classes are defined
def register_all_providers():
    """Import all provider implementations to register them."""
    try:
        importlib.import_module("sre_agent.providers.gitlab_provider")
    except ImportError:
        pass
    try:
        importlib.import_module("sre_agent.providers.circleci_provider")
    except ImportError:
        pass
    try:
        importlib.import_module("sre_agent.providers.jenkins_provider")
    except ImportError:
        pass
    try:
        importlib.import_module("sre_agent.providers.azuredevops_provider")
    except ImportError:
        pass
