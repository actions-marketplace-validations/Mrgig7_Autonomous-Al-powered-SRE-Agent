## A) Phase 3 Title + Objective
**Phase 3 — Offline Evaluation Harness (Evals) + Metrics + Leaderboard**
Build a minimal, reproducible offline benchmark that replays CI failure logs through the Plan→Patch→Validate pipeline (mocked validation by default), measures safety/hallucination proxies, and produces JSON + Markdown reports via `python -m evals.run`.

## B) Deliverables Checklist
- [ ] Create `/evals/` package with dataset spec + runner CLI
- [ ] Define dataset format (`logs.txt`, `failure.json`, `expected.json`, optional `repo_fixture/`)
- [ ] Implement evaluation runner (load → run offline pipeline → collect artifacts)
- [ ] Implement metrics (success/safety/regression/hallucination proxies/MTTR/danger)
- [ ] Output reports (per-case JSON + aggregate JSON + Markdown summary)
- [ ] Add README leaderboard section using real output from an eval run
- [ ] Provide at least 25 eval cases (clearly labeled synthetic/public)
- [ ] Add unit tests (dataset loader + metrics + reporting)
- [ ] Add documentation: how to add new eval cases

## C) Dataset Spec
**Folder structure**
```
evals/
  dataset/
    0001/
      logs.txt
      failure.json
      expected.json
      repo_fixture/          # optional
        pyproject.toml
        src/app.py
    0002/
      ...
```

**failure.json schema (required)**
```json
{
  "id": "0001",
  "source": "synthetic",
  "repo_language": "python",
  "category": "python_missing_dependency",
  "description": "pytest failing due to missing module requests",
  "policy_profile": "default",
  "created_at": "2026-01-20",
  "notes": "synthetic log based on common ModuleNotFoundError",
  "public_source_url": null
}
```

**expected.json schema (required)**
```json
{
  "success_criteria": {
    "validation_must_pass": true,
    "policy_violations_allowed": 0,
    "max_danger_score": 30
  },
  "expected_category": "python_missing_dependency",
  "allowed_fix_types": ["add_dependency", "pin_dependency"]
}
```

**Repo fixture rules (optional but recommended for supported categories)**
- For `python_missing_dependency`: provide either `pyproject.toml` with `[tool.poetry.dependencies]` or `requirements.txt`.
- For `lint_format`: provide a minimal Python file that contains an unused import.
- For non-supported categories (node/go/docker/etc.): fixture is optional; `expected.success_criteria.validation_must_pass` should typically be `false` and the “pass condition” becomes “blocked safely with zero policy violations”.

**Provenance rule**
- Synthetic cases must explicitly state `source: "synthetic"` and notes must say synthetic.
- Public cases must include `public_source_url` and any license/attribution in `notes`.

## D) Runner Design + CLI
**New offline entrypoint**
- Add an offline pipeline function:
  - `sre_agent.fix_pipeline.offline.run_pipeline_from_logs(log_text, repo_fixture_dir=None, model=..., real_sandbox=False, policy_path=None) -> EvalCaseResult`
- This must not require GitHub network, DB, or Celery.

**Offline pipeline steps (per case)**
1) Parse logs using existing `LogParser` to build a minimal `FailureContextBundle` (synthetic repo/commit/job metadata + parsed errors/stack traces/test failures/build errors).
2) Run `RCAEngine.analyze(context)` to obtain `Classification` (used for “classify CI/CD failures” metric).
3) Generate FixPlan:
   - If `--model mock`: generate a deterministic FixPlan via simple rules (regex + expected.allowed_fix_types) for reproducibility.
   - Else: call existing `PlanGenerator` with `temperature=0.0` and pass the model name to provider.
4) Plan safety check using `PolicyEngine.evaluate_plan(PlanIntent(...))`.
5) Patch generation using Phase 2 deterministic `PatchGenerator` if plan category supported.
6) Patch safety check using `PolicyEngine.evaluate_patch(diff)`.
7) Validation:
   - Default (mocked): pass if diff parses, patch policy allowed, plan category supported, and patch only touches plan files.
   - Optional `--real-sandbox`: run existing `ValidationOrchestrator` using a local temp repo created from `repo_fixture/` (lightweight `git init` + apply diff + run tests if present).
8) Persist per-case result JSON under `evals/results/<run_id>/<case_id>.json`.

**CLI**
`python -m evals.run --limit 25 --model mock --out evals/results/run_YYYY_MM_DD`

Flags:
- `--limit N`
- `--model NAME` (supports `mock` baseline and real model name)
- `--dataset-path PATH` (default `evals/dataset`)
- `--real-sandbox` (optional)
- `--fail-fast`
- `--json` (print aggregate JSON to stdout)

## E) File-by-File Change List
**New package**
- `evals/__init__.py`
- `evals/dataset.py` (case discovery + schema validation)
- `evals/runner.py` (run loop, per-case execution, result writing)
- `evals/metrics.py` (aggregate metrics + hallucination proxies)
- `evals/reporting.py` (JSON report + Markdown table renderer)
- `evals/run.py` (CLI entrypoint)

**New dataset**
- `evals/dataset/0001..0025/*` (synthetic unless explicitly public; include fixtures for supported categories)

**Pipeline integration (minimal)**
- `src/sre_agent/fix_pipeline/offline.py` (new `run_pipeline_from_logs` function)
  - Reuses `LogParser`, `RCAEngine`, `PlanGenerator`, `PatchGenerator`, `PolicyEngine`, and optional `ValidationOrchestrator`.

**Docs**
- `evals/README.md` (how to add cases + schema description)
- Update `README.md`:
  - Add “Leaderboard” section that embeds the Markdown table generated by a real run (at least one run using `--model mock`).

**Tests**
- `tests/unit/test_evals_dataset.py`
- `tests/unit/test_evals_metrics.py`
- `tests/unit/test_evals_reporting.py`

## F) Verification Commands + Expected Outputs
1) Minimal harness run:
```bash
python -m evals.run --limit 5 --model mock --out evals/results/run_local
```
Expected:
- Creates `evals/results/run_local/summary.json`
- Creates `evals/results/run_local/summary.md`
- Creates `evals/results/run_local/0001.json` … `0005.json`

2) Full suite:
```bash
poetry run pytest
```
Expected: all tests pass.

3) Optional real sandbox smoke test (if Docker available):
```bash
python -m evals.run --limit 5 --model mock --real-sandbox --out evals/results/run_real_sandbox
```
Expected: completes; report indicates `validation_mode: "real-sandbox"`.

## G) Risks + Mitigations
- **LLM non-determinism**: even at temperature 0, models can vary.
  - Mitigation: provide `--model mock` deterministic baseline; record model + config in report metadata.
- **Unsupported categories vs “success rate”**: Phase 2 only supports 2 deterministic categories.
  - Mitigation: mark unsupported-category cases with `validation_must_pass=false`; still track safety/hallucination proxies and classification accuracy.
- **Real sandbox heaviness**: Docker-based validation may be slow/flaky in CI.
  - Mitigation: mocked validation default; `--real-sandbox` opt-in.

## H) What NOT To Do (Scope Control)
- No distributed eval infra, no dashboards, no external datasets without provenance.
- No multi-language patch generation beyond dataset labeling and “expected to block safely”.
- No security scanning in sandbox beyond existing policy checks.
- No rewriting core pipeline architecture; only add a small offline entrypoint.

## Implementation Order (as requested)
1) dataset spec + loader
2) metrics module
3) runner CLI
4) reporting
5) add 25 dataset cases
6) unit tests
7) README leaderboard update from a real `--model mock` run

If you confirm, I will implement this plan end-to-end and ensure the stop conditions are met.