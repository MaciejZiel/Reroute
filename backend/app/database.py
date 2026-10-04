"""Database configuration and request-scoped sessions."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://reroute:local-only-change-me@localhost:5432/reroute",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for persisted models."""


def get_db() -> Generator[Session, None, None]:
    """Yield one database session per request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
