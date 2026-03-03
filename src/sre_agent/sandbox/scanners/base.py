from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sre_agent.schemas.validation import CommandResult


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return sha256_hex(normalized.encode("utf-8"))


def safe_json_loads(raw: str) -> object:
    return json.loads(raw) if raw.strip() else {}


def extract_version(stdout: str, pattern: str) -> str | None:
    m = re.search(pattern, stdout)
    return m.group(1) if m else None


def command_failed(result: CommandResult) -> bool:
    return result.timed_out or result.exit_code not in {0, 1}


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
