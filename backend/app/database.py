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
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
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
