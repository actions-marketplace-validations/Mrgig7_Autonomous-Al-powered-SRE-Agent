# Offline Evaluation Harness (Phase 3)

## Run

```bash
python -m evals.run --limit 35 --model mock --out evals/results/run_local
```

Outputs:

- `evals/results/<run_id>/0001.json` (per-case result + artifacts)
- `evals/results/<run_id>/summary.json` (aggregate + all case results)
- `evals/results/<run_id>/summary.md` (Markdown summary table)

Validation modes:

- Default: mocked validation (no Docker/network); passes when patch is valid + policy-allowed for supported categories.
- `--real-sandbox`: reserved for future Phase 4+ work; Phase 3 focuses on offline reproducibility.

## Dataset Format

Each case lives under `evals/dataset/<ID>/` where `<ID>` is 4 digits (`0001`, `0002`, …).

Required files:

- `logs.txt`: CI log excerpt (synthetic or public). Synthetic logs must be realistic and labeled as synthetic in `failure.json`.
- `failure.json`: case metadata
- `expected.json`: evaluation success criteria + allowed fix operations

Optional:

- `repo_fixture/`: lightweight “mini repo” containing the files required for deterministic patching (recommended for supported categories).

## failure.json schema

```json
{
  "id": "0001",
  "source": "synthetic",
  "repo_language": "python",
  "category": "python_missing_dependency",
  "description": "pytest failing due to missing module requests",
  "policy_profile": "default",
  "created_at": "2026-01-20",
  "notes": "synthetic log based on ModuleNotFoundError",
  "public_source_url": null
}
```

Rules:

- If `source` is `public`, `public_source_url` must be present.
- If `source` is `synthetic`, `public_source_url` must be `null`.

## expected.json schema

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

## Adding a New Case

1. Pick the next ID and create `evals/dataset/<ID>/`.
2. Add `logs.txt` with realistic output for the failure.
3. Add `failure.json` with correct provenance.
4. Add `expected.json`.
5. If the case is in a supported deterministic category, add `repo_fixture/` with the minimal files that the deterministic patch generator edits.
6. Run a small eval:

```bash
python -m evals.run --limit 5 --model mock --out evals/results/run_local
```
