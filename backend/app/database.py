"""Database configuration and request-scoped sessions."""

import logging
import os
import time
from collections.abc import Callable, Generator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://reroute:local-only-change-me@localhost:5432/reroute",
)
# A request must not wait for the database for long: a slow statement is cancelled after
# STATEMENT_TIMEOUT_MS and a request that cannot get a pooled connection fails after
# POOL_TIMEOUT_S (both end as a 503, see ``main.py``).
STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))
POOL_TIMEOUT_S = float(os.getenv("DB_POOL_TIMEOUT_S", "5"))


def make_engine(
    statement_timeout_ms: int | None = None, pool_timeout_s: float = 30.0, url: str | None = None
) -> Engine:
    options = f"-c statement_timeout={statement_timeout_ms}" if statement_timeout_ms else None
    return create_engine(
        url or DATABASE_URL,
        pool_pre_ping=True,
        pool_timeout=pool_timeout_s,
        connect_args={"options": options} if options else {},
    )


# The API serves requests with this engine.
engine = make_engine(STATEMENT_TIMEOUT_MS, POOL_TIMEOUT_S)
# Migrations, the collector, the timetable import and the training export run long statements.
admin_engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AdminSession = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)
logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for persisted models."""


def get_db() -> Generator[Session, None, None]:
    """Yield one database session per request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def wait_for_database(
    target: Engine = engine,
    *,
    attempts: int = 30,
    delay_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until the database accepts connections, retrying with a growing delay.

    The database can still be starting (or restarting after its init scripts) when the API or
    the collector boots. Raises the last error once ``attempts`` are used up.
    """
    for attempt in range(1, attempts + 1):
        try:
            with target.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OperationalError as error:
            if attempt == attempts:
                raise
            wait = min(delay_s * attempt, 10.0)
            logger.warning(
                "database not ready (attempt %d/%d): %s; retrying in %.0fs",
                attempt,
                attempts,
                str(error).splitlines()[0],
                wait,
            )
            sleep(wait)
