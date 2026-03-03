from __future__ import annotations

from sre_agent.adapters.go import GoAdapter


def test_go_adapter_detects_missing_go_sum() -> None:
    adapter = GoAdapter()
    repo_files = ["go.mod", "main.go"]
    log_text = "go: github.com/acme/foo@v1.2.3: missing go.sum entry\n"
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.category == "go_mod_tidy"
    assert det.confidence >= 0.8


def test_go_adapter_detects_missing_module_provider() -> None:
    adapter = GoAdapter()
    repo_files = ["go.mod", "cmd/app/main.go"]
    log_text = "no required module provides package github.com/acme/foo/bar; to add it:\n"
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.category == "go_add_missing_module"


def test_go_validation_steps_are_deterministic() -> None:
    steps = GoAdapter().build_validation_steps("/workspace")
    assert [s.command for s in steps] == ["go mod tidy", "go test ./..."]
