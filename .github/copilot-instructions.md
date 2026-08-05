# Copilot Instructions — Autonomous AI-powered SRE Agent

## Build, Test, and Lint

This project uses **Poetry** for Python dependency management.

```bash
# Install all dependencies (including dev)
poetry install --with dev

# Lint
poetry run ruff check .
poetry run black --check .
poetry run mypy src

# Format
poetry run black .
poetry run ruff check --fix .

# Run full test suite
poetry run pytest

# Run a single test file
poetry run pytest tests/unit/test_fix_pipeline.py

# Run a single test by name
poetry run pytest -k "test_function_name"

# Run with coverage
poetry run pytest --cov=sre_agent
```

### Frontend (React dashboard in `frontend/`)

```bash
cd frontend
npm install
npm run dev      # Vite dev server
npm run build    # TypeScript check + Vite build
npm run lint     # TypeScript type check (tsc --noEmit)
```

### Docker

```bash
docker-compose up -d   # Starts PostgreSQL, Redis, API, Celery worker, frontend
```

## Architecture

The system is an autonomous SRE agent that monitors CI/CD pipelines, detects failures, performs root cause analysis, generates code fixes, and creates pull requests.

### Core Pipeline Flow

1. **Webhook ingestion** (`src/sre_agent/api/webhooks/`) — receives events from 5 CI providers (GitHub, GitLab, CircleCI, Jenkins, Azure DevOps)
2. **Event normalization** (`services/event_normalizer.py`) — converts provider-specific payloads to a unified `NormalizedPipelineEvent` schema
3. **Log parsing** (`services/log_parser.py`) — extracts structured failure information from build logs
4. **Adapter detection** (`adapters/`) — language/framework-specific adapters (Python, Node, Go, Java, Docker) identify failure categories
5. **Fix pipeline** (`fix_pipeline/orchestrator.py`) — orchestrates AI-powered fix generation with AST safety guards
6. **Safety layer** (`safety/`) — policy engine with danger scoring, diff validation, and runtime constraints
7. **Consensus** (`consensus/`) — multi-model agreement before applying fixes
8. **PR creation** (`pr/`) — generates pull requests with explainability reports

### Key Subsystems

- **Providers** (`providers/`) — abstract `BaseCIProvider` interface for CI platform integrations
- **Adapters** (`adapters/`) — abstract `BaseAdapter` per language/framework for detection and validation
- **Celery tasks** (`tasks/`) — async task dispatch for pipeline processing and notifications
- **Safety policy** (`config/safety_policy.yaml`) — YAML-driven rules governing what fixes are allowed
- **Schemas** (`schemas/`) — Pydantic models defining data contracts between subsystems

### Infrastructure

- **FastAPI** async API server with lifespan management
- **PostgreSQL** via async SQLAlchemy + Alembic migrations
- **Redis** as Celery broker and caching layer
- **Docker sandbox** for safe fix validation in isolation

## Key Conventions

### Python

- Python 3.11+ — use `collections.abc` types (not `typing`), `X | None` union syntax
- Pydantic v2 for all data models and settings (`BaseModel`, `BaseSettings`)
- Async-first: database access, HTTP clients, and Redis are all async
- Line length: 100 characters (Black + Ruff)
- Ruff rules: E, F, I, N, W, UP (with E501 and UP007 ignored)
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` — no need to mark individual async tests

### Project Structure

- All source code lives under `src/sre_agent/` (Poetry package layout with `packages = [{include = "sre_agent", from = "src"}]`)
- API routes are organized by domain in `api/` with each module exporting a `router`
- Webhook handlers are separated by provider under `api/webhooks/`
- Pydantic schemas are separate from SQLAlchemy models (`schemas/` vs `models/`)
- Configuration is loaded from environment variables via `pydantic-settings` (`config.py`)

### API Patterns

- Response envelope is opt-in via `?envelope=true` query param, `X-Response-Envelope` header, or `Accept: application/vnd.sre.enveloped+json`
- All API routes are prefixed with `/api/v1`
- JWT-based authentication (`auth/`)

### Database

- Alembic migrations live in `alembic/versions/`
- Tests use in-memory SQLite (`sqlite+aiosqlite:///:memory:`) for speed; integration tests can use testcontainers for PostgreSQL

### Adding a New CI Provider

1. Create `providers/<name>_provider.py` implementing `BaseCIProvider`
2. Create `api/webhooks/<name>.py` with a FastAPI router
3. Register the router in `main.py`

### Adding a New Language Adapter

1. Create `adapters/<lang>.py` implementing `BaseAdapter`
2. Register in `adapters/registry.py`
3. Add corresponding unit tests in `tests/unit/`
