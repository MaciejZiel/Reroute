"""Current vehicle positions: read from ``vehicle_latest``, with a direct-fetch fallback.

The collector keeps ``vehicle_latest`` fresh. When it is not running (a plain local start with
only the API and the database) the API polls the feed itself, at most every 12 seconds, and
fills the same table, so the rest of the code has a single source.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from gtfs_rt_kit import GtfsRtClient, GtfsRtKitError, parse_feed
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .collector import store, tables
from .collector.config import Settings
from .collector.service import Pipeline, write_delay_rollup
from .database import admin_engine

logger = logging.getLogger(__name__)
FRESH_FOR = timedelta(seconds=120)
MAX_VEHICLE_AGE = timedelta(seconds=180)
FALLBACK_REFRESH_S = 12


@dataclass(frozen=True)
class LiveVehicle:
    vehicle_id: str
    label: str | None
    trip_id: str | None
    service_date: object
    route_id: str | None
    line: str
    mode: str
    latitude: float
    longitude: float
    bearing: int | None
    observed_at: datetime
    next_stop_seq: int | None
    next_stop_id: str | None
    scheduled_s: int | None
    delay_s: int | None
    match_status: int

    @property
    def is_stale(self) -> bool:
        return datetime.now(UTC) - self.observed_at > MAX_VEHICLE_AGE

    @property
    def has_delay(self) -> bool:
        return self.delay_s is not None and not self.is_stale


@dataclass(frozen=True)
class LiveSnapshot:
    vehicles: tuple[LiveVehicle, ...]
    observed_at: datetime | None
    source: str  # "collector", "direct" or "none"


def _rows(db: Session, line: str | None = None, trip_ids: list[str] | None = None) -> list:
    latest = tables.vehicle_latest
    query = select(latest).where(latest.c.observed_at > datetime.now(UTC) - timedelta(minutes=5))
    if line is not None:
        query = query.where(latest.c.line == line)
    if trip_ids is not None:
        query = query.where(latest.c.trip_id.in_(trip_ids))
    return list(db.execute(query).all())


def _to_vehicle(row) -> LiveVehicle:
    return LiveVehicle(
        vehicle_id=row.vehicle_id,
        label=row.label,
        trip_id=row.trip_id,
        service_date=row.service_date,
        route_id=row.route_id,
        line=row.line,
        mode=row.mode,
        latitude=row.lat,
        longitude=row.lon,
        bearing=row.bearing,
        observed_at=row.observed_at,
        next_stop_seq=row.next_stop_seq,
        next_stop_id=row.next_stop_id,
        scheduled_s=row.scheduled_s,
        delay_s=row.delay_s,
        match_status=row.match_status,
    )


_fallback_lock = threading.Lock()
_fallback_until = 0.0
_fallback_pipeline: Pipeline | None = None
_fallback_error: str | None = None


def collector_is_fresh(db: Session) -> bool:
    """A collector polled recently (even if the feed was stale), or positions are recent."""
    beat, newest = db.execute(
        select(
            select(tables.collector_state.c.updated_at)
            .where(tables.collector_state.c.key == store.HEARTBEAT_KEY)
            .scalar_subquery(),
            select(func.max(tables.vehicle_latest.c.observed_at)).scalar_subquery(),
        )
    ).one()
    now = datetime.now(UTC)
    return any(stamp is not None and now - stamp < FRESH_FOR for stamp in (beat, newest))


def ensure_fresh(db: Session) -> bool | None:
    """Fetch the feed in-process when no collector keeps ``vehicle_latest`` current.

    Never makes a request wait for somebody else's fetch: when another request holds the lock the
    caller serves what ``vehicle_latest`` has. Returns whether a collector is fresh, or ``None``
    when that was not looked up (a fetch was attempted recently).
    """
    global _fallback_until, _fallback_pipeline, _fallback_error
    if time.monotonic() < _fallback_until:
        return None
    if collector_is_fresh(db):
        return True
    if not _fallback_lock.acquire(blocking=False):
        return False
    try:
        if time.monotonic() < _fallback_until:
            return False
        _fallback_until = time.monotonic() + FALLBACK_REFRESH_S
        settings = Settings.from_env()
        if _fallback_pipeline is None:
            _fallback_pipeline = Pipeline(admin_engine, settings)
        try:
            with GtfsRtClient(timeout=8.0) as client:
                feed = parse_feed(client.fetch_bytes(settings.vehicles_url))
            pipeline = _fallback_pipeline
            pipeline.refresh_static()
            result = pipeline.derive(feed)
            store.upsert_latest(admin_engine, result.observations)
            store.prune_latest(admin_engine, datetime.now(UTC))
            write_delay_rollup(admin_engine, datetime.now(UTC))
            _fallback_error = None
        except (GtfsRtKitError, OSError) as error:
            _fallback_error = str(error)
            logger.warning("direct feed fetch failed: %s", error)
    finally:
        _fallback_lock.release()
    return False


def fallback_error() -> str | None:
    return _fallback_error


def snapshot(db: Session, line: str | None = None) -> LiveSnapshot:
    """Current vehicles (optionally of one line)."""
    collector = ensure_fresh(db)
    vehicles = tuple(_to_vehicle(row) for row in _rows(db, line))
    if not vehicles:
        return LiveSnapshot((), None, "none")
    newest = max(vehicle.observed_at for vehicle in vehicles)
    if collector is None:
        collector = collector_is_fresh(db)
    return LiveSnapshot(vehicles, newest, "collector" if collector else "direct")


def vehicles_of_trips(db: Session, trip_ids: list[str]) -> dict[str, LiveVehicle]:
    """Live vehicles by trip id (collector data only: cheap, no feed fetch)."""
    if not trip_ids:
        return {}
    return {row.trip_id: _to_vehicle(row) for row in _rows(db, trip_ids=trip_ids) if row.trip_id}


def vehicle_by_id(db: Session, vehicle_id: str) -> LiveVehicle | None:
    ensure_fresh(db)
    row = db.execute(
        select(tables.vehicle_latest).where(tables.vehicle_latest.c.vehicle_id == vehicle_id)
    ).first()
    return _to_vehicle(row) if row else None
