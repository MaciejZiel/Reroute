"""A request never waits for long: fallback fetch lock, statement timeout, pool timeout."""

import threading
import time
from datetime import UTC, datetime

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app import live
from app.collector import store
from app.database import make_engine
from app.main import app


def test_a_busy_fallback_fetch_is_not_waited_for(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "_fallback_until", 0.0)

    def no_fetch(*args: object, **kwargs: object) -> None:
        raise AssertionError("a second fetch started while one was running")

    monkeypatch.setattr(live, "GtfsRtClient", no_fetch)
    finished: list[float] = []

    def request() -> None:
        started = time.monotonic()
        with Session(db.get_bind()) as session:
            live.ensure_fresh(session)
        finished.append(time.monotonic() - started)

    with live._fallback_lock:  # another request is fetching the feed right now
        thread = threading.Thread(target=request)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive(), "ensure_fresh blocked on the fallback lock"
    assert finished[0] < 2


def test_snapshot_asks_about_the_collector_once(
    db: Session, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_state(pg_engine, store.HEARTBEAT_KEY, datetime.now(UTC).isoformat())
    monkeypatch.setattr(live, "_fallback_until", 0.0)
    calls = []
    original = live.collector_is_fresh
    monkeypatch.setattr(live, "collector_is_fresh", lambda s: calls.append(1) or original(s))

    live.snapshot(db)

    assert len(calls) == 1


def test_a_slow_statement_is_cancelled(pg_engine: Engine) -> None:
    limited = make_engine(statement_timeout_ms=200, url=pg_engine.url.render_as_string(False))
    try:
        started = time.monotonic()
        with pytest.raises(DBAPIError, match="statement timeout"), limited.connect() as connection:
            connection.execute(text("SELECT pg_sleep(5)"))
        assert time.monotonic() - started < 3
    finally:
        limited.dispose()


def test_an_exhausted_pool_fails_fast(pg_engine: Engine) -> None:
    url = pg_engine.url.render_as_string(False)
    tiny = create_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.3)
    try:
        with tiny.connect():
            started = time.monotonic()
            with pytest.raises(PoolTimeoutError), tiny.connect():
                pass
            assert time.monotonic() - started < 2
    finally:
        tiny.dispose()


def test_api_engine_has_a_short_pool_timeout() -> None:
    from app.database import POOL_TIMEOUT_S, engine

    assert engine.pool.timeout() == POOL_TIMEOUT_S <= 10


def test_database_trouble_is_a_503_not_a_500() -> None:
    router = APIRouter()

    @router.get("/_test/pool")
    def pool() -> None:
        raise PoolTimeoutError("QueuePool limit reached")

    app.include_router(router)
    response = TestClient(app, raise_server_exceptions=False).get("/_test/pool")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
