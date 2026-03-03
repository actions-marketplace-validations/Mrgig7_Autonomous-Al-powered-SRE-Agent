from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from evals.runner import run_evals


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.run")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", type=str, required=True)
    p.add_argument("--dataset-path", type=Path, default=Path("evals") / "dataset")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--real-sandbox", action="store_true")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--json", action="store_true", dest="print_json")
    return p.parse_args()


def _default_out_dir() -> Path:
    return Path("evals") / "results" / "run_local"


def main() -> None:
    args = _parse_args()
    out_dir = args.out or _default_out_dir()
    summary = asyncio.run(
        run_evals(
            dataset_path=args.dataset_path,
            out_dir=out_dir,
            model=args.model,
            limit=args.limit,
            real_sandbox=args.real_sandbox,
            fail_fast=args.fail_fast,
        )
    )
    if args.print_json:
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
