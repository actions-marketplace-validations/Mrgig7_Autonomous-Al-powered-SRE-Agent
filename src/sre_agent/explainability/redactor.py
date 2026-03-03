from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sre_agent.config import get_settings
from sre_agent.safety.policy_loader import load_policy_from_file


@dataclass(frozen=True)
class Redactor:
    patterns: list[re.Pattern[str]]
    url_token_pattern: re.Pattern[str]
    header_token_pattern: re.Pattern[str]

    def redact_text(self, value: str) -> str:
        redacted = value
        redacted = self.url_token_pattern.sub(r"\1=[REDACTED]", redacted)
        redacted = self.header_token_pattern.sub(r"\1 [REDACTED]", redacted)
        for pat in self.patterns:
            redacted = pat.sub("[REDACTED]", redacted)
        return redacted

    def redact_obj(self, obj: Any) -> Any:
        if obj is None:
            return None
        if isinstance(obj, str):
            return self.redact_text(obj)
        if isinstance(obj, list):
            return [self.redact_obj(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        return obj


@lru_cache(maxsize=1)
def get_redactor() -> Redactor:
    settings = get_settings()
    policy = load_policy_from_file(settings.safety_policy_path)
    patterns = [re.compile(p, re.IGNORECASE) for p in policy.secrets.forbidden_patterns]
    url_token_pattern = re.compile(
        r"(?i)\b(access_token|token|auth|authorization|signature|sig|key)=([^&\s]+)"
    )
    header_token_pattern = re.compile(r"(?i)\b(authorization|x-api-key|x-auth-token):\s*([^\s]+)")
    return Redactor(
        patterns=patterns,
        url_token_pattern=url_token_pattern,
        header_token_pattern=header_token_pattern,
    )
