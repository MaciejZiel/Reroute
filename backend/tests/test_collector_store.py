"""Collector storage on PostGIS: partitions, idempotent writes, alerts and incremental backfill."""

import gzip
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from geoalchemy2.shape import from_shape
from google.transit import gtfs_realtime_pb2
from gtfs_rt_kit import Alert, EntitySelector, TimeRange, TranslatedString, Translation
from shapely.geometry import Point
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.collector import partitions, store, tables
from app.collector.backfill import run_backfill
from app.collector.config import Settings
from app.collector.derive import VehicleState
from app.collector.metrics import Metrics
from app.collector.service import Pipeline, run, write_delay_rollup
from app.models import PatternStop, Route, Stop, StopPattern, TripSchedule
from app.timeutil import service_instant

DAY = date(2026, 10, 13)
TRIP = "2026-10-13:9:X:1:0800"
LONS = (21.00, 21.01, 21.02, 21.03)


def _seed_timetable(db: Session) -> None:
    db.add(Route(id="9", short_name="9", long_name="x", mode="tram", is_demo=False))
    db.add_all(
        Stop(
            id=name,
            name=name,
            latitude=52.23,
            longitude=lon,
            location=from_shape(Point(lon, 52.23), srid=4326),
            is_demo=False,
        )
        for name, lon in zip("abcd", LONS, strict=True)
    )
    db.flush()
    db.add(StopPattern(id=0, stop_ids="a,b,c,d"))
    db.flush()
    db.add_all(PatternStop(pattern_id=0, position=i, stop_id=s) for i, s in enumerate("abcd"))
    base = 8 * 3600
    db.add(
        TripSchedule(
            trip_id=TRIP,
            route_id="9",
            pattern_id=0,
            departures=",".join(str(base + 300 * i) for i in range(4)),
            service_date=DAY,
            headsign="d",
            direction_id=0,
        )
    )
    db.commit()


def _feed_bytes(lon: float, seconds_after_origin: int) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    moment = service_instant(DAY, seconds_after_origin)
    feed.header.timestamp = int(moment.timestamp())
    entity = feed.entity.add()
    entity.id = "V/9/1"
    vehicle = entity.vehicle
    vehicle.trip.trip_id = TRIP
    vehicle.vehicle.id = "V/9/1"
    vehicle.vehicle.label = "1001"
    vehicle.position.latitude = 52.23
    vehicle.position.longitude = lon
    vehicle.timestamp = int(moment.timestamp())
    return feed.SerializeToString()


def _write_raw(raw: Path, name: str, payload: bytes) -> None:
    folder = raw / name.split("/")[0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name.split("/")[1]).write_bytes(gzip.compress(payload))


def _count(engine: Engine, table) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table)) or 0


def test_partitions_are_created_on_demand_and_expired_ones_dropped(
    db: Session, pg_engine: Engine
) -> None:
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    with pg_engine.begin() as connection:
        partitions.ensure_observation_partitions(
            connection, [date(2026, 10, 1), date(2026, 10, 12), date(2026, 10, 13)]
        )
        partitions.ensure_arrival_partitions(connection, [date(2026, 6, 15), date(2026, 10, 13)])
        partitions.ensure_observation_partitions(connection, [date(2026, 10, 13)])  # idempotent
        dropped = partitions.drop_expired(connection, now, 7, 90)
        remaining = partitions._partitions(connection, partitions.OBSERVATIONS)
        arrivals = partitions._partitions(connection, partitions.ARRIVALS)

    assert dropped == ["vehicle_observations_p20261001", "stop_arrival_events_p202606"]
    assert "vehicle_observations_p20261012" in remaining
    assert "stop_arrival_events_p202610" in arrivals


def test_poll_writes_are_idempotent_and_slim(db: Session, pg_engine: Engine) -> None:
    _seed_timetable(db)
    pipeline = Pipeline(pg_engine, Settings())
    pipeline.refresh_static()
    from gtfs_rt_kit import parse_feed

    first = parse_feed(_feed_bytes(21.005, 8 * 3600 + 180))
    second = parse_feed(_feed_bytes(21.015, 8 * 3600 + 480))
    before = _count(pg_engine, tables.vehicle_observations)
    events_before = _count(pg_engine, tables.stop_arrival_events)

    pipeline.process(first)
    result = pipeline.process(second)
    # The same observations arriving again (overlapping live and backfill runs) change nothing.
    pipeline.state.clear()
    pipeline.process(first)
    pipeline.process(second)

    assert _count(pg_engine, tables.vehicle_observations) - before == 2
    assert _count(pg_engine, tables.stop_arrival_events) - events_before == 1
    assert result.arrivals[0]["stop_id"] == "b"
    with pg_engine.connect() as connection:
        row = connection.execute(
            select(tables.vehicle_observations).order_by(tables.vehicle_observations.c.observed_at)
        ).first()
    assert row is not None and row.delay_s == 30 and row.line == "9"
    pipeline_rows = result.observations
    store.upsert_latest(pg_engine, pipeline_rows)
    store.upsert_latest(pg_engine, pipeline_rows)  # upsert, not duplicate
    assert _count(pg_engine, tables.vehicle_latest) == 1
    assert write_delay_rollup(pg_engine, datetime.now(UTC)) in (0, 1)


def test_restore_state_rebuilds_progress_from_latest_positions(
    db: Session, pg_engine: Engine
) -> None:
    from gtfs_rt_kit import parse_feed

    _seed_timetable(db)
    pipeline = Pipeline(pg_engine, Settings())
    result = pipeline.process(parse_feed(_feed_bytes(21.015, 8 * 3600 + 450)))
    store.upsert_latest(pg_engine, result.observations)

    fresh = Pipeline(pg_engine, Settings())
    assert fresh.restore_state() == 1
    state = fresh.state["V/9/1"]
    assert isinstance(state, VehicleState) and 1.4 < state.progress < 1.6


def test_alerts_are_upserted_with_their_entities(pg_engine: Engine) -> None:
    alert = Alert(
        entity_id="a1",
        active_periods=(TimeRange(datetime(2026, 10, 13, tzinfo=UTC), None),),
        informed_entities=(EntitySelector(route_id="9"), EntitySelector(stop_id="b")),
        cause="CONSTRUCTION",
        effect="DETOUR",
        header_text=TranslatedString((Translation("Objazd", "pl"), Translation("Detour", "en"))),
    )
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    assert store.upsert_alerts(pg_engine, [alert], now) == 1
    assert store.upsert_alerts(pg_engine, [alert], now + timedelta(minutes=1)) == 1

    with pg_engine.connect() as connection:
        row = connection.execute(select(tables.alerts)).one()
        entities = connection.execute(select(tables.alert_entities)).all()
        connection.execute(text("TRUNCATE alerts CASCADE"))
        connection.commit()
    assert (row.header_pl, row.header_en, row.effect) == ("Objazd", "Detour", "DETOUR")
    assert row.first_seen == now and row.last_seen == now + timedelta(minutes=1)
    assert {(e.route_id, e.stop_id) for e in entities} == {("9", None), (None, "b")}


def test_backfill_resumes_from_the_last_processed_file(
    db: Session, pg_engine: Engine, tmp_path: Path
) -> None:
    _seed_timetable(db)
    raw = tmp_path / "raw"
    _write_raw(raw, "2026-10-13/v_075900.pb.gz", _feed_bytes(21.005, 8 * 3600 + 180))
    _write_raw(raw, "2026-10-13/v_080400.pb.gz", _feed_bytes(21.015, 8 * 3600 + 480))
    (raw / "2026-10-13" / "v_080500.pb.gz").write_bytes(b"not gzip")

    first = run_backfill(pg_engine, Pipeline(pg_engine, Settings()), raw)
    assert (first.files, first.unreadable, first.arrivals) == (2, 1, 1)
    assert first.last_file == "2026-10-13/v_080400.pb.gz"

    unchanged = run_backfill(pg_engine, Pipeline(pg_engine, Settings()), raw)
    assert unchanged.files == 0 and unchanged.observations == 0

    # A new snapshot arrives; the run continues from the stored position and the vehicle's
    # progress (primed from the last processed file) yields the arrival at stop c.
    _write_raw(raw, "2026-10-13/v_081000.pb.gz", _feed_bytes(21.025, 8 * 3600 + 780))
    (raw / "2026-10-13" / "v_080500.pb.gz").unlink()
    more = run_backfill(pg_engine, Pipeline(pg_engine, Settings()), raw)
    assert (more.files, more.observations, more.arrivals) == (1, 1, 1)
    with pg_engine.connect() as connection:
        sources = set(connection.execute(select(tables.vehicle_observations.c.source)).scalars())
        stops = list(connection.execute(select(tables.stop_arrival_events.c.stop_id)).scalars())
    assert sources == {1} and sorted(stops) == ["b", "c"]


def test_health_turns_unhealthy_when_polls_stop() -> None:
    metrics = Metrics(poll_interval_s=20)
    assert metrics.healthy()  # start-up grace
    metrics.success()
    assert metrics.healthy()
    metrics.last_success -= 61
    assert not metrics.healthy()
    assert "reroute_collector_polls_ok_total 0" in metrics.render()


def test_live_loop_writes_a_heartbeat_even_when_the_feed_is_down(
    db: Session, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("signal.signal", lambda *args: None)  # keep pytest's handlers
    down = "http://127.0.0.1:9/vehicles.pb"
    settings = Settings(vehicles_url=down, alerts_url=down)

    run(pg_engine, settings, once=True)

    assert store.get_state(pg_engine, store.HEARTBEAT_KEY) is not None
