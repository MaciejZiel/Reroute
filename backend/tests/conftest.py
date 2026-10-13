"""Shared fixtures. Database tests run only when TEST_DATABASE_URL points at a *_test database."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a database ending in _test (it is wiped)")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    from app.migrate import migrate

    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(pg_engine: Engine) -> Iterator[Session]:
    """A session on the migrated test database; rows written by a test are removed after it."""
    session = Session(pg_engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        tables = ", ".join(
            f'"{name}"'
            for name in ("trip_schedules", "pattern_stops", "stop_patterns", "route_stops")
        )
        session.execute(
            text(
                f"TRUNCATE {tables}, routes, stops, vehicle_observations, stop_arrival_events, "
                "vehicle_latest, collector_state, delay_snapshots CASCADE"
            )
        )
        session.commit()
        session.close()
