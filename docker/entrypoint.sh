#!/usr/bin/env sh
# Container entrypoint for the SRE Agent backend.
# - Optionally waits for Postgres
# - Runs alembic migrations (skip with SKIP_DB_MIGRATIONS=true)
# - Execs the provided command (uvicorn, celery, etc.)

set -e

log() {
    printf '[entrypoint] %s\n' "$*"
}

# --- Wait for Postgres if DATABASE_URL points at a host:port we can reach ---
wait_for_postgres() {
    if [ "${SKIP_DB_WAIT:-false}" = "true" ]; then
        return 0
    fi

    # Extract host:port from DATABASE_URL (postgresql+asyncpg://user:pw@host:port/db)
    url="${DATABASE_URL:-}"
    case "$url" in
        postgres*|postgresql*) ;;
        *) return 0 ;;
    esac

    hostport=$(printf '%s' "$url" | sed -E 's#^[^@]+@([^/?]+).*$#\1#')
    host=$(printf '%s' "$hostport" | cut -d: -f1)
    port=$(printf '%s' "$hostport" | cut -s -d: -f2)
    port=${port:-5432}

    if [ -z "$host" ]; then
        return 0
    fi

    log "Waiting for Postgres at ${host}:${port}..."
    i=0
    until python -c "import socket,sys; s=socket.socket(); s.settimeout(2); \
sys.exit(0) if s.connect_ex(('${host}', ${port})) == 0 else sys.exit(1)" >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge 60 ]; then
            log "Postgres did not become reachable after 60 attempts; continuing anyway"
            return 0
        fi
        sleep 1
    done
    log "Postgres is reachable."
}

run_migrations() {
    if [ "${SKIP_DB_MIGRATIONS:-false}" = "true" ]; then
        log "SKIP_DB_MIGRATIONS=true — skipping alembic upgrade"
        return 0
    fi

    if [ ! -f "/app/alembic.ini" ]; then
        log "No alembic.ini found at /app/alembic.ini — skipping migrations"
        return 0
    fi

    log "Running alembic upgrade head..."
    if alembic -c /app/alembic.ini upgrade head; then
        log "Migrations applied."
    else
        log "alembic upgrade head failed (exit $?). Continuing so the process can surface a clearer error."
    fi
}

wait_for_postgres
run_migrations

log "Starting: $*"
exec "$@"
