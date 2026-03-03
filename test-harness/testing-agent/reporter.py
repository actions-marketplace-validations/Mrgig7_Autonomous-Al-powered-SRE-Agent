from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    from rich.console import Console
    from rich.table import Table
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments
    Console = None
    Table = None


class ValidatorOutcome(BaseModel):
    name: str
    passed: bool
    duration_seconds: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class HarnessReport(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    failure_id: int | str
    branch: str
    event_id: str | None = None
    run_id: str | None = None
    repository: str
    validations: list[ValidatorOutcome] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.validations)


def render_console(report: HarnessReport) -> None:
    if Console is None or Table is None:
        print(f"Testing Agent Report ({report.repository} / {report.branch})")
        for result in report.validations:
            status = "PASS" if result.passed else "FAIL"
            notes = result.error or ", ".join(f"{k}={v}" for k, v in result.details.items())
            print(f"- {result.name}: {status} ({result.duration_seconds:.2f}s) {notes}")
        print(f"Overall: {'PASS' if report.passed else 'FAIL'}")
        return

    console = Console()
    table = Table(title=f"Testing Agent Report ({report.repository} / {report.branch})")
    table.add_column("Validator", justify="left")
    table.add_column("Result", justify="center")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Notes", justify="left")

    for result in report.validations:
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        notes = result.error or ", ".join(f"{k}={v}" for k, v in result.details.items())
        table.add_row(result.name, status, f"{result.duration_seconds:.2f}", notes)

    console.print(table)
    console.print(f"Overall: {'PASS' if report.passed else 'FAIL'}")


def write_json(report: HarnessReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
