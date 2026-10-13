"""The collector's live loop: poll, derive, write."""

from __future__ import annotations

import gzip
import logging
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gtfs_rt_kit import Feed, GtfsRtClient, GtfsRtKitError, parse_feed
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from ..data_sources import WARSAW_BOUNDS
from ..models import FeedMetadata
from . import store, tables
from .config import Settings
from .derive import MatchStatus, Observation, PollResult, VehicleState, derive_poll, project
from .metrics import Metrics, serve

logger = logging.getLogger(__name__)
FEED_ID = "warsaw-static-gtfs"


def to_observations(feed: Feed) -> list[Observation]:
    """Vehicle positions of a feed that lie in the Warsaw area and carry a timestamp."""
    min_lon, min_lat, max_lon, max_lat = WARSAW_BOUNDS
    observations = []
    for vehicle in feed.vehicles:
        position = vehicle.position
        observed_at = vehicle.timestamp or feed.timestamp
        if position is None or observed_at is None:
            continue
        if not (
            min_lat <= position.latitude <= max_lat and min_lon <= position.longitude <= max_lon
        ):
            continue
        descriptor = vehicle.vehicle
        observations.append(
            Observation(
                vehicle_id=vehicle.vehicle_id,
                label=descriptor.label if descriptor else None,
                trip_id=vehicle.trip_id or None,
                latitude=position.latitude,
                longitude=position.longitude,
                bearing=position.bearing,
                speed=position.speed,
                observed_at=observed_at,
            )
        )
    return observations


class Pipeline:
    """Feed in, rows out: shared by the live loop and the backfill."""

    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.cache = store.TimetableCache(engine)
        self.state: dict[str, VehicleState] = {}

    def resolve_line(self, obs: Observation) -> tuple[str, str, str | None]:
        name = route_from_vehicle_id_str(obs.vehicle_id)
        if name:
            found = self.cache.route_for_name(name)
            if found:
                return found[1], found[2], found[0]
            return name, "bus", None
        return "?", "bus", None

    def refresh_static(self) -> None:
        with Session(self.engine) as session:
            version = session.scalar(
                select(FeedMetadata.downloaded_at).where(FeedMetadata.id == FEED_ID)
            )
            self.cache.refresh_static(session, version)

    def derive(self, feed: Feed, source: int = 0) -> PollResult:
        observations = to_observations(feed)
        return derive_poll(
            observations, self.cache, self.state, feed.timestamp, source, self.resolve_line
        )

    def process(self, feed: Feed, source: int = 0) -> PollResult:
        self.refresh_static()
        result = self.derive(feed, source)
        store.write_poll(self.engine, result, self.settings.store_unmatched)
        return result

    def restore_state(self) -> int:
        """Rebuild per-vehicle progress from vehicle_latest after a restart."""
        self.refresh_static()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    tables.vehicle_latest.c.vehicle_id,
                    tables.vehicle_latest.c.trip_id,
                    tables.vehicle_latest.c.observed_at,
                    tables.vehicle_latest.c.lat,
                    tables.vehicle_latest.c.lon,
                )
            ).all()
        timetables = self.cache(row.trip_id for row in rows if row.trip_id)
        restored = 0
        for row in rows:
            timetable = timetables.get(row.trip_id) if row.trip_id else None
            projection = project(row.lat, row.lon, timetable) if timetable else None
            if projection is None:
                continue
            self.state[row.vehicle_id] = VehicleState(
                row.trip_id, row.observed_at, projection.progress, int(projection.progress)
            )
            restored += 1
        return restored


def route_from_vehicle_id_str(vehicle_id: str) -> str | None:
    parts = vehicle_id.split("/")
    return parts[1] if len(parts) == 3 and parts[0] == "V" and parts[1] else None


def write_delay_rollup(engine: Engine, now: datetime) -> int:
    """One per-line row per minute for the punctuality view, from the latest positions."""
    minute = now.replace(second=0, microsecond=0)
    with engine.begin() as connection:
        if connection.execute(
            text("SELECT 1 FROM delay_snapshots WHERE observed_at = :minute LIMIT 1"),
            {"minute": minute},
        ).first():
            return 0
        result = connection.execute(
            text(
                "INSERT INTO delay_snapshots (observed_at, line, mode, vehicles, "
                "mean_delay_seconds, median_delay_seconds, early, on_time, late) "
                "SELECT :minute, line, mode, count(*), round(avg(delay_s))::int, "
                "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s))::int, "
                "count(*) FILTER (WHERE delay_s < -60), "
                "count(*) FILTER (WHERE delay_s BETWEEN -60 AND 180), "
                "count(*) FILTER (WHERE delay_s > 180) "
                "FROM vehicle_latest WHERE match_status = :ok AND observed_at > :since "
                "GROUP BY line, mode"
            ),
            {
                "minute": minute,
                "ok": int(MatchStatus.OK),
                "since": now - timedelta(seconds=90),
            },
        )
        connection.execute(
            text("DELETE FROM delay_snapshots WHERE observed_at < :cutoff"),
            {"cutoff": now - timedelta(days=7)},
        )
    return result.rowcount


def archive_raw(directory: str, payload: bytes, moment: datetime) -> None:
    moment = moment.astimezone(UTC)
    target = Path(directory) / f"{moment:%Y-%m-%d}" / f"v_{moment:%H%M%S}.pb.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(payload, compresslevel=6))


def run(engine: Engine, settings: Settings, once: bool = False) -> None:
    metrics = Metrics(settings.poll_interval_s)
    server = None if once else serve(metrics, settings.metrics_port)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    pipeline = Pipeline(engine, settings)
    pipeline.restore_state()
    store.housekeeping(
        engine,
        datetime.now(UTC),
        settings.observation_retention_days,
        settings.arrival_retention_days,
    )
    last_alerts = last_housekeeping = last_rollup = 0.0
    failures = 0
    with GtfsRtClient() as client:
        while not stop.is_set():
            started = time.monotonic()
            try:
                payload = client.fetch_bytes(settings.vehicles_url)
                feed = parse_feed(payload)
                if settings.raw_archive_dir:
                    archive_raw(
                        settings.raw_archive_dir, payload, feed.timestamp or datetime.now(UTC)
                    )
                result = pipeline.process(feed)
                store.upsert_latest(engine, result.observations)
                store.prune_latest(engine, datetime.now(UTC))
                matched = sum(row["delay_s"] is not None for row in result.observations)
                metrics.count("polls_ok_total")
                metrics.count("observations_written_total", len(result.observations))
                metrics.count("arrival_events_written_total", len(result.arrivals))
                metrics.count("duplicates_skipped_total", result.skipped_duplicates)
                metrics.gauge("vehicles_in_feed", len(feed.vehicles))
                metrics.gauge("matched_share", matched / max(len(result.observations), 1))
                if feed.timestamp:
                    metrics.gauge(
                        "feed_age_seconds", (datetime.now(UTC) - feed.timestamp).total_seconds()
                    )
                metrics.success()
                failures = 0
            except (GtfsRtKitError, OSError) as error:
                failures += 1
                metrics.count("polls_failed_total")
                logger.warning("poll failed (%d in a row): %s", failures, error)
            except Exception:
                failures += 1
                metrics.count("polls_failed_total")
                logger.exception("poll failed (%d in a row)", failures)
            now = time.monotonic()
            metrics.gauge("last_poll_duration_seconds", round(now - started, 3))
            try:
                store.set_state(engine, store.HEARTBEAT_KEY, datetime.now(UTC).isoformat())
                if now - last_alerts >= settings.alerts_interval_s:
                    last_alerts = now
                    poll_alerts(engine, client, settings, metrics)
                if now - last_rollup >= 60:
                    last_rollup = now
                    write_delay_rollup(engine, datetime.now(UTC))
                if now - last_housekeeping >= 3600:
                    last_housekeeping = now
                    dropped = store.housekeeping(
                        engine,
                        datetime.now(UTC),
                        settings.observation_retention_days,
                        settings.arrival_retention_days,
                    )
                    if dropped:
                        logger.info("dropped expired partitions: %s", ", ".join(dropped))
            except Exception:
                logger.exception("maintenance step failed")
            delay = settings.poll_interval_s * (1 if failures == 0 else min(2**failures, 6))
            if once:
                break
            stop.wait(max(delay - (time.monotonic() - started), 1.0))
    if server:
        server.shutdown()


def poll_alerts(engine: Engine, client: GtfsRtClient, settings: Settings, metrics: Metrics) -> None:
    try:
        alerts = client.alerts(settings.alerts_url)
    except GtfsRtKitError as error:
        metrics.count("alerts_polls_failed_total")
        logger.warning("alerts poll failed: %s", error)
        return
    metrics.gauge("alerts_active", store.upsert_alerts(engine, alerts, datetime.now(UTC)))
