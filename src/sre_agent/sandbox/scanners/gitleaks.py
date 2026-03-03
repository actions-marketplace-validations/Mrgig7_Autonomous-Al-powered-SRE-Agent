from __future__ import annotations

import time

from sre_agent.config import get_settings
from sre_agent.sandbox.docker_sandbox import DockerSandbox
from sre_agent.sandbox.scanners.base import (
    command_failed,
    extract_version,
    safe_json_loads,
    sha256_path,
)
from sre_agent.schemas.scans import GitleaksFinding, GitleaksScanResult, ScanStatus


def parse_gitleaks_report(json_text: str) -> list[GitleaksFinding]:
    data = safe_json_loads(json_text)
    findings: list[GitleaksFinding] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("RuleID") or item.get("RuleId") or "unknown")
            file_path = str(item.get("File") or item.get("FilePath") or "")
            if not file_path:
                continue
            findings.append(GitleaksFinding(rule_id=rule_id, file_path_hash=sha256_path(file_path)))
    return findings


async def run_gitleaks_scan(sandbox: DockerSandbox) -> GitleaksScanResult:
    settings = get_settings()
    started = time.perf_counter()

    version_result = await sandbox.run_command(
        "gitleaks version", timeout=settings.scanner_timeout_seconds
    )
    version = extract_version(version_result.stdout, r"(\d+\.\d+\.\d+)") or None

    report_path = "/tmp/gitleaks.json"
    cmd = (
        "gitleaks detect --source . --no-git --redact "
        f"--report-format json --report-path {report_path}"
    )
    run_result = await sandbox.run_command(cmd, timeout=settings.scanner_timeout_seconds)

    report_result = await sandbox.run_command(f"cat {report_path} || true", timeout=10)
    duration = time.perf_counter() - started

    if command_failed(run_result) and not report_result.stdout.strip():
        return GitleaksScanResult(
            status=ScanStatus.ERROR,
            version=version,
            duration_seconds=duration,
            error_message=run_result.stderr.strip() or "gitleaks failed",
        )

    findings = parse_gitleaks_report(report_result.stdout.strip())

    status = ScanStatus.FAIL if findings else ScanStatus.PASS
    return GitleaksScanResult(
        status=status,
        version=version,
        duration_seconds=duration,
        findings_count=len(findings),
        findings=findings,
    )
