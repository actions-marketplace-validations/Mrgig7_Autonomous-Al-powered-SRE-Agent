from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from api_client import SREApiClient
from reporter import HarnessReport, ValidatorOutcome, render_console, write_json
from validators import (
    event_ingestion,
    fix_pipeline,
    observability,
    pull_request,
    rca_engine,
    sandbox,
    security_safety,
)

from config import load_settings

VALIDATORS = [
    event_ingestion.validate,
    rca_engine.validate,
    fix_pipeline.validate,
    security_safety.validate,
    sandbox.validate,
    pull_request.validate,
    observability.validate,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Testing Agent validator runner")
    parser.add_argument("--failure-id", required=True, help="Failure case ID (1..9)")
    parser.add_argument("--branch", required=True, help="Failure branch name")
    parser.add_argument("--repository", required=True, help="GitHub repository owner/repo")
    parser.add_argument("--event-id", default=None, help="Known SRE failure_id UUID")
    parser.add_argument(
        "--output",
        default="test-harness/reports/latest-validator-report.json",
        help="Output JSON path",
    )
    parser.add_argument("--dry-run", action="store_true", help="List validators and exit")
    return parser.parse_args()


async def _discover_event_id(context: dict[str, Any], sre_client: SREApiClient) -> str | None:
    payload = await sre_client.get_dashboard_events(
        repository=context["repository"],
        branch=context["branch"],
        limit=20,
    )
    events = payload.get("events", [])
    if not isinstance(events, list) or not events:
        return None
    newest = events[0]
    event_id = newest.get("id")
    return str(event_id) if event_id else None


async def run() -> int:
    args = parse_args()
    if args.dry_run:
        print("Validators:")
        for validator in VALIDATORS:
            print(f"- {validator.__module__}.{validator.__name__}")
        return 0

    settings = load_settings()
    context: dict[str, Any] = {
        "failure_id": args.failure_id,
        "branch": args.branch,
        "repository": args.repository,
        "event_id": args.event_id,
        "sse_wait_timeout_seconds": settings.sse_wait_timeout_seconds,
        "github_token": settings.github_token,
        "github_owner": settings.github_owner,
        "github_api_base_url": settings.github_api_base_url,
    }

    sre_client = SREApiClient(base_url=settings.sre_base_url)
    outcomes: list[ValidatorOutcome] = []
    try:
        await sre_client.login(settings.sre_auth_email, settings.sre_auth_password)
        if not context["event_id"]:
            discovered = await _discover_event_id(context, sre_client)
            context["event_id"] = discovered
        if not context["event_id"]:
            outcomes.append(
                ValidatorOutcome(
                    name="bootstrap",
                    passed=False,
                    error="Could not discover failure event for repository/branch",
                )
            )
        else:
            context["failure_id"] = str(context["event_id"])

        for validator in VALIDATORS:
            outcome = await validator(context, sre_client)
            outcomes.append(outcome)
            if not outcome.passed:
                # Strict mode: continue running all validators for complete report.
                continue
    finally:
        await sre_client.close()

    report = HarnessReport(
        failure_id=args.failure_id,
        branch=args.branch,
        event_id=str(context.get("failure_id")) if context.get("failure_id") else None,
        run_id=str(context.get("run_id")) if context.get("run_id") else None,
        repository=args.repository,
        validations=outcomes,
    )

    render_console(report)
    write_json(report, Path(args.output))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
