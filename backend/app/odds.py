"""How a line usually runs at a stop, from the recorded arrivals: the "chance of a delay" view.

Plain counting, no model: for one line at one platform, on one kind of day at one hour, what
share of the recorded arrivals of the last 28 days were more than 3 or 5 minutes late. The
counting is a materialized view (migration 0005) refreshed once a night in a background thread
of the collector, so a request reads a few hundred precomputed rows.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")
ODDS_DAYS = 28
REFRESH_HOUR = 3  # Warsaw time: after the night service, before the morning peak
VIEW = "stop_delay_odds"


STATE_KEY = "odds:refreshed_at"
RETRY_AFTER = timedelta(hours=1)
_POPULATED = text("SELECT relispopulated FROM pg_class WHERE relname = :name")


def is_populated(connection: Connection | Session) -> bool:
    return bool(connection.execute(_POPULATED, {"name": VIEW}).scalar())


def refresh(engine: Engine) -> None:
    """Recount; CONCURRENTLY once the view holds data, so readers are never blocked."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        concurrently = "CONCURRENTLY " if is_populated(connection) else ""
        connection.execute(text(f"REFRESH MATERIALIZED VIEW {concurrently}{VIEW}"))


def last_boundary(now: datetime) -> datetime:
    """The latest REFRESH_HOUR (Warsaw time) at or before ``now``: a count older than this is
    due, whatever happened in between (restarts, a collector down for days)."""
    local = now.astimezone(WARSAW)
    boundary = local.replace(hour=REFRESH_HOUR, minute=0, second=0, microsecond=0)
    return boundary if local >= boundary else boundary - timedelta(days=1)


def due(now: datetime, refreshed_at: datetime | None, populated: bool) -> bool:
    return not populated or refreshed_at is None or refreshed_at < last_boundary(now)


class OddsRefresher:
    """Refreshes the view in a background thread once a night, so the poll loop never waits for
    the scan. When it last succeeded is kept in the collector's state table, so a restart
    neither recounts needlessly nor skips a night; a failed count is tried again an hour later."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.thread: threading.Thread | None = None
        self.failed_at: datetime | None = None

    def refreshed_at(self) -> datetime | None:
        from .collector import store

        value = store.get_state(self.engine, STATE_KEY)
        return datetime.fromisoformat(value) if value else None

    def tick(self, now: datetime) -> bool:
        if self.thread is not None and self.thread.is_alive():
            return False
        if self.failed_at is not None and now - self.failed_at < RETRY_AFTER:
            return False
        with self.engine.connect() as connection:
            populated = is_populated(connection)
        if not due(now, self.refreshed_at(), populated):
            return False
        self.thread = threading.Thread(target=self._run, name="odds-refresh", daemon=True)
        self.thread.start()
        return True

    def _run(self) -> None:
        from .collector import store

        started = datetime.now(UTC)
        try:
            refresh(self.engine)
        except Exception:
            self.failed_at = datetime.now(UTC)
            logger.exception("refreshing %s failed; trying again in an hour", VIEW)
            return
        self.failed_at = None
        store.set_state(self.engine, STATE_KEY, started.isoformat())
        logger.info("refreshed %s in %.0f s", VIEW, (datetime.now(UTC) - started).total_seconds())


def station_odds(db: Session, stop_ids: Sequence[str]) -> list[dict]:
    """Every precomputed row for the platforms of one station."""
    if not stop_ids:
        return []
    rows = db.execute(
        text(
            f"SELECT line, stop_id, day_type, hour, n, days, late3, late5, early1, median_s, "
            f"p90_s, first_day, last_day FROM {VIEW} WHERE stop_id = ANY(:ids)"
        ),
        {"ids": list(stop_ids)},
    ).mappings()
    return [dict(row) for row in rows]
