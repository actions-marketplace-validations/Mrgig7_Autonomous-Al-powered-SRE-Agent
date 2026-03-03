# Sample App (Task Manager Platform)

This is the failure-prone application used by the `test-harness`.

It contains:
- `backend/` FastAPI + SQLAlchemy CRUD service
- `frontend/` React + TypeScript dashboard
- `tests/` backend-focused tests
- `.github/workflows/sample-app-ci.yml` CI pipeline

Failure scenarios are applied per-branch via `failure_injections.py`.

