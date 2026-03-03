"""Run Alembic migrations with sync driver and write error to file."""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

os.environ["DATABASE_URL"] = (
    "postgresql+psycopg2://sre_agent:sre_agent_password@localhost:5432/sre_agent"
)

try:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    with open("migration_log.txt", "w") as f:
        f.write("Migration complete!\n")
    print("Migration complete!")
except Exception:
    tb = traceback.format_exc()
    with open("migration_log.txt", "w") as f:
        f.write(tb)
    print(tb[-200:])
    sys.exit(1)
