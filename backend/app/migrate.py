"""Apply schema changes: base tables from the models, then numbered SQL files in order.

``python -m app.migrate`` is safe to run any number of times. Applied files are recorded in
``schema_migrations``; every file is idempotent so databases created by older versions
(which only had ``create_all``) upgrade in place.
"""

import logging
import time
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

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
    ensure_arrival_time_index(target)  # outside the transaction: it builds concurrently
    return done


ARRIVAL_INDEX = "ix_stop_arrival_events_actual_at"
_INDEX_LOCK = 727402


def ensure_arrival_time_index(target: Engine = engine) -> list[str]:
    """Block-range index on ``stop_arrival_events(actual_at)``, built while the collector runs.

    The delay model reads the last hour of arrivals of a few lines and stops; rows arrive in time
    order, so a BRIN index is tiny and almost free to maintain. A plain ``CREATE INDEX`` on a
    partitioned table cannot be concurrent and blocks inserts while it scans the partition, so
    this creates the parent index ``ON ONLY`` (instant), builds one index per existing partition
    ``CONCURRENTLY`` and attaches it. Partitions created later get their index automatically.
    Idempotent; returns the partitions indexed now. Roll back with ``drop_arrival_time_index``.
    """
    built = []
    with target.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f"SELECT pg_advisory_lock({_INDEX_LOCK})"))
        try:
            _politely(
                connection,
                f"CREATE INDEX IF NOT EXISTS {ARRIVAL_INDEX} "
                "ON ONLY stop_arrival_events USING brin (actual_at)",
            )
            for partition in connection.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = 'stop_arrival_events'::regclass ORDER BY 1"
                )
            ).scalars():
                if _has_attached_index(connection, partition):
                    continue
                name = f"{partition}_actual_at_idx"
                # A failed concurrent build leaves an invalid index behind: replace it.
                connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
                connection.execute(
                    text(f"CREATE INDEX CONCURRENTLY {name} ON {partition} USING brin (actual_at)")
                )
                connection.execute(text(f"ALTER INDEX {ARRIVAL_INDEX} ATTACH PARTITION {name}"))
                built.append(partition)
                logger.info("built the arrival time index on %s", partition)
        finally:
            connection.execute(text(f"SELECT pg_advisory_unlock({_INDEX_LOCK})"))
    return built


def _politely(connection, statement: str, attempts: int = 120) -> None:
    """Run a statement that needs a brief table lock without making writers queue behind it.

    A lock request waits for every open writer, and every later writer waits for the request. So
    ask for at most 200 ms and try again a second later, until the table is quiet for a moment.
    """
    connection.execute(text("SET lock_timeout = '200ms'"))
    try:
        for attempt in range(attempts):
            try:
                connection.execute(text(statement))
                return
            except OperationalError as error:
                if "lock timeout" not in str(error) or attempt == attempts - 1:
                    raise
                time.sleep(1.0)
    finally:
        connection.execute(text("RESET lock_timeout"))


def _has_attached_index(connection, partition: str) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT 1 FROM pg_inherits i JOIN pg_index x ON x.indexrelid = i.inhrelid "
                "WHERE i.inhparent = CAST(:parent AS regclass) "
                "AND x.indrelid = CAST(:part AS regclass)"
            ),
            {"parent": ARRIVAL_INDEX, "part": partition},
        ).first()
    )


def drop_arrival_time_index(target: Engine = engine) -> None:
    """The down path: remove the index (a short lock per partition) and forget the old
    ``0004_arrival_time_index.sql`` record, if a database had applied it."""
    with target.begin() as connection:
        connection.execute(text(f"DROP INDEX IF EXISTS {ARRIVAL_INDEX}"))
        connection.execute(
            text("DELETE FROM schema_migrations WHERE name = '0004_arrival_time_index.sql'")
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.migrate", description=__doc__)
    parser.add_argument(
        "--drop-arrival-time-index",
        action="store_true",
        help="roll back the arrival time index (the delay model then reads arrivals more slowly)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.drop_arrival_time_index:
        drop_arrival_time_index()
        print("Dropped the arrival time index")
        return
    done = migrate()
    print(f"Applied {len(done)} migration(s): {', '.join(done) or 'database is up to date'}")


if __name__ == "__main__":
    main()
