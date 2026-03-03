from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from sre_agent.config import get_settings
from sre_agent.safety.policy_engine import PolicyEngine
from sre_agent.safety.policy_loader import load_policy_from_file
from sre_agent.safety.policy_models import SafetyPolicy

logger = logging.getLogger(__name__)


@lru_cache
def get_policy_engine() -> PolicyEngine:
    settings = get_settings()
    policy_path = Path(settings.safety_policy_path)
    try:
        policy = load_policy_from_file(policy_path)
    except Exception as e:
        logger.warning(
            "Failed to load safety policy; using defaults",
            extra={"policy_path": str(policy_path), "error": str(e)},
        )
        policy = SafetyPolicy()
    return PolicyEngine(policy)
