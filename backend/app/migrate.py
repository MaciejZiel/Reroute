"""Apply schema changes: base tables from the models, then numbered SQL files in order.

``python -m app.migrate`` is safe to run any number of times. Applied files are recorded in
``schema_migrations``; every file is idempotent so databases created by older versions
(which only had ``create_all``) upgrade in place.
"""

import logging
from pathlib import Path

from sqlalchemy import Engine, text

from . import models  # noqa: F401 — register SQLAlchemy models before create_all.
from .database import Base, engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
logger = logging.getLogger(__name__)


def pending_migrations(applied: set[str]) -> list[Path]:
    return [path for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if path.name not in applied]


def migrate(target: Engine = engine) -> list[str]:
    """Bring the database up to date and return the names of the files applied now."""
    with target.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=target)
    with target.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        # Serialise concurrent starters (api and collector boot together).
        connection.execute(text("SELECT pg_advisory_xact_lock(727401)"))
        applied = set(connection.execute(text("SELECT name FROM schema_migrations")).scalars())
        done = []
        for path in pending_migrations(applied):
            # exec_driver_sql: the files contain colons (::) that text() would treat as binds.
            connection.exec_driver_sql(path.read_text(encoding="utf-8"))
            connection.execute(
                text("INSERT INTO schema_migrations (name) VALUES (:name)"), {"name": path.name}
            )
            done.append(path.name)
            logger.info("applied migration %s", path.name)
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    done = migrate()
    print(f"Applied {len(done)} migration(s): {', '.join(done) or 'database is up to date'}")


if __name__ == "__main__":
    main()
