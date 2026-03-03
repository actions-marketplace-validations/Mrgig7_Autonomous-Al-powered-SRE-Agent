from __future__ import annotations

import gzip
import time
from pathlib import Path

from sre_agent.config import get_settings
from sre_agent.sandbox.docker_sandbox import DockerSandbox
from sre_agent.sandbox.scanners.base import extract_version, sha256_hex, write_bytes
from sre_agent.schemas.scans import SbomResult, ScanStatus


async def generate_sbom(sandbox: DockerSandbox, *, run_id: str) -> tuple[SbomResult, Path | None]:
    settings = get_settings()
    started = time.perf_counter()

    version_result = await sandbox.run_command(
        "syft version", timeout=settings.scanner_timeout_seconds
    )
    version = extract_version(version_result.stdout, r"Syft\s+(\d+\.\d+\.\d+)") or extract_version(
        version_result.stdout, r"(\d+\.\d+\.\d+)"
    )

    sbom_cmd = "syft dir:. -o json"
    sbom_out = await sandbox.run_command(sbom_cmd, timeout=settings.scanner_timeout_seconds)
    duration = time.perf_counter() - started

    if sbom_out.exit_code != 0 or sbom_out.timed_out:
        return (
            SbomResult(
                status=ScanStatus.ERROR,
                version=version,
                duration_seconds=duration,
                error_message=sbom_out.stderr.strip() or "syft failed",
            ),
            None,
        )

    sbom_bytes = sbom_out.stdout.encode("utf-8", errors="replace")
    sbom_sha = sha256_hex(sbom_bytes)
    gz_bytes = gzip.compress(sbom_bytes, compresslevel=6)

    artifacts_dir = Path(settings.artifacts_dir)
    rel_path = Path("sbom") / f"{run_id}.syft.json.gz"
    full_path = artifacts_dir / rel_path
    write_bytes(full_path, gz_bytes)

    return (
        SbomResult(
            status=ScanStatus.GENERATED,
            version=version,
            duration_seconds=duration,
            path=str(rel_path).replace("\\", "/"),
            sha256=sbom_sha,
            size_bytes=len(gz_bytes),
        ),
        full_path,
    )
