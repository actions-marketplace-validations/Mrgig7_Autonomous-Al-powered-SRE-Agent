from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from sre_agent.safety.policy_models import SafetyPolicy


def load_policy_from_file(path: str | Path) -> SafetyPolicy:
    policy_path = Path(path)
    raw = policy_path.read_text(encoding="utf-8")

    data: Any
    if policy_path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(raw) or {}
    elif policy_path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        raise ValueError(f"Unsupported policy file format: {policy_path.suffix}")

    return SafetyPolicy.model_validate(data)
