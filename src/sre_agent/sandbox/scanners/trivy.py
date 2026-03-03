from __future__ import annotations

import time
from collections import Counter

from sre_agent.config import get_settings
from sre_agent.sandbox.docker_sandbox import DockerSandbox
from sre_agent.sandbox.scanners.base import command_failed, extract_version, safe_json_loads
from sre_agent.schemas.scans import ScanStatus, TrivyPackageSummary, TrivyScanResult


def _severity_rank(sev: str) -> int:
    order = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return order.get(sev.upper(), 0)


def _fails_threshold(severity_counts: dict[str, int], threshold: str) -> bool:
    t = threshold.upper()
    for sev, count in severity_counts.items():
        if count and _severity_rank(sev) >= _severity_rank(t):
            return True
    return False


def parse_trivy_report(json_text: str) -> tuple[dict[str, int], list[TrivyPackageSummary]]:
    data = safe_json_loads(json_text)
    severity_counts: Counter[str] = Counter()
    package_counts: Counter[str] = Counter()

    results = data.get("Results") if isinstance(data, dict) else []
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, dict):
                continue
            vulns = r.get("Vulnerabilities") or []
            if not isinstance(vulns, list):
                continue
            for v in vulns:
                if not isinstance(v, dict):
                    continue
                sev = str(v.get("Severity") or "UNKNOWN").upper()
                severity_counts[sev] += 1
                pkg = v.get("PkgName")
                if pkg:
                    package_counts[str(pkg)] += 1

    top = [
        TrivyPackageSummary(name=name, count=count) for name, count in package_counts.most_common(5)
    ]
    return dict(severity_counts), top


async def run_trivy_scan(sandbox: DockerSandbox) -> TrivyScanResult:
    settings = get_settings()
    started = time.perf_counter()

    version_result = await sandbox.run_command(
        "trivy --version", timeout=settings.scanner_timeout_seconds
    )
    version = extract_version(version_result.stdout, r"Version:\s*v?(\d+\.\d+\.\d+)") or None

    report_path = "/tmp/trivy.json"
    cmd = (
        "trivy fs --format json --skip-db-update --skip-java-db-update " f"--output {report_path} ."
    )
    run_result = await sandbox.run_command(cmd, timeout=settings.scanner_timeout_seconds)
    report_result = await sandbox.run_command(f"cat {report_path} || true", timeout=10)
    duration = time.perf_counter() - started

    if command_failed(run_result) and not report_result.stdout.strip():
        return TrivyScanResult(
            status=ScanStatus.ERROR,
            version=version,
            duration_seconds=duration,
            threshold=settings.fail_on_vuln_severity,
            error_message=run_result.stderr.strip() or "trivy failed",
        )

    severity_counts, top = parse_trivy_report(report_result.stdout)
    total = sum(severity_counts.values())

    fails = _fails_threshold(severity_counts, settings.fail_on_vuln_severity)
    status = ScanStatus.FAIL if fails else ScanStatus.PASS
    return TrivyScanResult(
        status=status,
        version=version,
        duration_seconds=duration,
        total_vulnerabilities=total,
        severity_counts=severity_counts,
        top_packages=top,
        threshold=settings.fail_on_vuln_severity,
    )
