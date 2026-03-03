from __future__ import annotations

from pathlib import Path


class FailureInjectionError(RuntimeError):
    pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _replace_once(path: Path, old: str, new: str) -> None:
    content = _read(path)
    if old not in content:
        raise FailureInjectionError(f"Expected snippet not found in {path}")
    _write(path, content.replace(old, new, 1))


def inject_failure(sample_root: Path, failure_id: int) -> str:
    if failure_id == 1:
        _replace_once(
            sample_root / "backend/requirements.txt",
            "alembic==1.13.2\n",
            "",
        )
        return "Missing dependency: alembic omitted from backend requirements"

    if failure_id == 2:
        cfg = sample_root / "backend/app/config.py"
        content = _read(cfg)
        if "import os" not in content:
            content = "import os\n" + content
        target = 'secret_key: str = Field(default="dev-secret-key", alias="SECRET_KEY")'
        replacement = (
            'secret_key: str = Field(default=os.environ["UNDEFINED_SECRET_KEY"],'
            ' alias="SECRET_KEY")'
        )
        if target not in content:
            raise FailureInjectionError("Could not inject invalid env var reference")
        _write(cfg, content.replace(target, replacement, 1))
        return "Invalid env var reference: UNDEFINED_SECRET_KEY"

    if failure_id == 3:
        _replace_once(
            sample_root / "tests/test_tasks.py",
            'assert any(task["title"] == "Write tests" for task in body)\n',
            'assert any(task["title"] == "Write tests" for task in body)\n    assert 1 == 2\n',
        )
        return "Failing unit test assertion injected"

    if failure_id == 4:
        _replace_once(
            sample_root / "backend/Dockerfile",
            "COPY app /app/app\n",
            "COPY ./nonexistent /app\n",
        )
        return "Dockerfile COPY path broken"

    if failure_id == 5:
        _replace_once(
            sample_root / ".env.example",
            "SECRET_KEY=replace-with-secure-value\n",
            "SECRET_KEY=replace-with-secure-value\nAPI_KEY=sk-live-FAKE12345HARD_CODED\n",
        )
        return "Hardcoded fake API key injected"

    if failure_id == 6:
        _replace_once(
            sample_root / "frontend/src/services/api.ts",
            "  return response.data;\n",
            "  return response.data.items;\n",
        )
        return "TypeScript response type mismatch injected"

    if failure_id == 7:
        _replace_once(
            sample_root / "backend/alembic/versions/20260211_0001_init_tasks.py",
            'sa.Column("title", sa.String(length=255), nullable=False),\n',
            'sa.Column("task_title", sa.String(length=255), nullable=False),\n',
        )
        return "Migration/model column mismatch injected"

    if failure_id == 8:
        _replace_once(
            sample_root / "tests/test_import_error.py",
            "from app.main import app\n",
            "from app.nonexistent import app\n",
        )
        return "Wrong import path injected into test"

    if failure_id == 9:
        _replace_once(
            sample_root / ".github/workflows/sample-app-ci.yml",
            "uses: actions/checkout@v4",
            "uses: actions/nonexistent@v1",
        )
        return "Invalid GitHub Action step injected"

    raise FailureInjectionError(f"Unknown failure id: {failure_id}")
