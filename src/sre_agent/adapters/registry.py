from __future__ import annotations

from dataclasses import dataclass

from sre_agent.adapters.base import BaseAdapter, DetectionResult
from sre_agent.adapters.docker import DockerAdapter
from sre_agent.adapters.go import GoAdapter
from sre_agent.adapters.java import JavaAdapter
from sre_agent.adapters.node import NodeAdapter
from sre_agent.adapters.python import PythonAdapter


@dataclass(frozen=True)
class SelectedAdapter:
    adapter: BaseAdapter
    detection: DetectionResult


_adapters: list[BaseAdapter] = [
    PythonAdapter(),
    NodeAdapter(),
    JavaAdapter(),
    GoAdapter(),
    DockerAdapter(),
]


def register_adapters(adapters: list[BaseAdapter]) -> None:
    _adapters.clear()
    _adapters.extend(adapters)


def get_adapters() -> list[BaseAdapter]:
    return list(_adapters)


def select_adapter(log_text: str, repo_files: list[str]) -> SelectedAdapter | None:
    best: SelectedAdapter | None = None
    for adapter in _adapters:
        detection = adapter.detect(log_text, repo_files)
        if detection is None:
            continue
        candidate = SelectedAdapter(adapter=adapter, detection=detection)
        if best is None or candidate.detection.confidence > best.detection.confidence:
            best = candidate
    return best
