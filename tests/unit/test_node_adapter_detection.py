from __future__ import annotations

import json

from sre_agent.adapters.node import NodeAdapter


def test_node_adapter_detects_missing_dependency() -> None:
    adapter = NodeAdapter()
    repo_files = ["package.json", "src/index.js"]
    log_text = "Error: Cannot find module 'lodash'\n"
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.repo_language == "node"
    assert det.category == "node_missing_dependency"
    assert det.confidence >= 0.8


def test_node_adapter_detects_lockfile_mismatch() -> None:
    adapter = NodeAdapter()
    repo_files = ["package.json", "package-lock.json"]
    log_text = "npm ERR! package-lock.json is out of date, please run npm install\n"
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.category == "node_lockfile_mismatch"


def test_node_validation_steps_include_lint_when_present(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "scripts": {"lint": "eslint ."}}),
        encoding="utf-8",
    )
    steps = NodeAdapter().build_validation_steps(str(tmp_path))
    cmds = [s.command for s in steps]
    assert "npm ci" in cmds[0]
    assert "npm test" in cmds[1]
    assert any(c == "npm run lint" for c in cmds)
