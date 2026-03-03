from __future__ import annotations

from sre_agent.adapters.docker import DockerAdapter


def test_docker_adapter_detects_base_image_not_found() -> None:
    adapter = DockerAdapter()
    repo_files = ["Dockerfile", "app.py"]
    log_text = "failed to solve: ubuntu:99.99: not found: manifest unknown\n"
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.category == "docker_pin_base_image"
    assert det.confidence >= 0.7


def test_docker_validation_steps() -> None:
    steps = DockerAdapter().build_validation_steps("/workspace")
    assert steps[0].command.startswith("docker build")
