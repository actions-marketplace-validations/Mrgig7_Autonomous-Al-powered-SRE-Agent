from __future__ import annotations

from sre_agent.adapters.registry import select_adapter


def test_select_adapter_picks_highest_confidence() -> None:
    repo_files = ["package.json", "package-lock.json", "src/index.js"]
    log_text = """
    npm ERR! code MODULE_NOT_FOUND
    Error: Cannot find module 'express'
    """
    selected = select_adapter(log_text, repo_files)
    assert selected is not None
    assert selected.adapter.name == "node"
    assert selected.detection.category == "node_missing_dependency"
    assert selected.detection.confidence >= 0.8
