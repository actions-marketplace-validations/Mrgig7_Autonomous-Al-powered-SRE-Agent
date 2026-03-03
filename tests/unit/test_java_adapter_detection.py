from __future__ import annotations

from sre_agent.adapters.java import JavaAdapter


def test_java_adapter_detects_missing_dependency_version() -> None:
    adapter = JavaAdapter()
    repo_files = ["pom.xml", "src/main/java/App.java"]
    log_text = (
        "The project com.acme:demo:1.0.0 has 1 error\n"
        "'dependencies.dependency.version' for org.junit.jupiter:junit-jupiter is missing.\n"
    )
    det = adapter.detect(log_text, repo_files)
    assert det is not None
    assert det.category == "java_dependency_version_missing"
    assert det.confidence >= 0.7


def test_java_validation_steps_use_maven_when_pom_present(tmp_path) -> None:
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    steps = JavaAdapter().build_validation_steps(str(tmp_path))
    assert steps[0].command.startswith("mvn")
