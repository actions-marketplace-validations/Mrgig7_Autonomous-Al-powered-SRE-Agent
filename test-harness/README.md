# Real-Time Test Harness

The harness creates a controlled, failure-prone sample repository and validates the full autonomous SRE loop:

1. Push failure branch to GitHub.
2. GitHub Actions fails.
3. GitHub webhook reaches SRE Agent.
4. SRE pipeline runs (ingestion -> RCA -> plan -> patch -> scans -> sandbox -> PR).
5. Testing Agent validates every subsystem and emits a structured report.

## Structure

```text
test-harness/
├── sample-app/               # Failure-prone application template
├── testing-agent/            # API-driven subsystem validators
├── run_harness.py            # Orchestrates repo provisioning + execution
├── docker-compose.test.yml   # Local sample-app stack
├── Makefile                  # Convenience commands
└── reports/                  # Generated JSON reports
```

## Prerequisites

- Running SRE Agent API/worker stack.
- Publicly reachable webhook URL for SRE ingestion.
  - Example: tunnel URL ending at `/webhooks/github`.
- GitHub token with repository + webhook permissions.

## Required Environment Variables

```bash
GITHUB_TOKEN=...
SRE_BASE_URL=http://localhost:8000
SRE_WEBHOOK_URL=https://public-url.example.com/webhooks/github
SRE_AUTH_EMAIL=operator@example.com
SRE_AUTH_PASSWORD=password123
```

Optional:

```bash
GITHUB_OWNER=override-owner-for-pr-validation
GITHUB_API_BASE_URL=https://api.github.com
GITHUB_WEBHOOK_SECRET=match-your-sre-agent-webhook-secret
```

## Usage

### Setup

```bash
cd test-harness
make setup
```

### Dry run

```bash
make dry-run
```

### Run all failure scenarios

```bash
python run_harness.py --failures all
```

### Run a subset

```bash
python run_harness.py --failures 1,4,7
```

### Cleanup created GitHub repo after run

```bash
python run_harness.py --failures all --cleanup
```

## Reports

- Per-case validator reports:
  - `test-harness/reports/validator-failure-XX.json`
- Aggregate harness report:
  - `test-harness/reports/harness-report.json`

## Verification Commands

```bash
cd test-harness/sample-app/backend && python -m py_compile app/main.py
cd test-harness/testing-agent && python -m pytest tests -v
python -c "import yaml; yaml.safe_load(open('test-harness/sample-app/.github/workflows/sample-app-ci.yml', encoding='utf-8'))"
```
