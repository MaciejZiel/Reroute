"""Trip dates, headsigns and stop lookups produced by the GTFS import and the migrations."""

import csv
import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app import data_sources
from app.migrate import migrate, pending_migrations
from app.models import PatternStop, TripSchedule


def _csv(archive: zipfile.ZipFile, name: str, rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(name, buffer.getvalue())


def _archive(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        _csv(
            archive,
            "routes.txt",
            [{"route_id": "9", "route_short_name": "9", "route_long_name": "x", "route_type": "0"}],
        )
        _csv(
            archive,
            "stops.txt",
            [
                {"stop_id": "a", "stop_name": "Alfa", "stop_lat": "52.23", "stop_lon": "21.01"},
                {"stop_id": "b", "stop_name": "Beta", "stop_lat": "52.22", "stop_lon": "21.01"},
            ],
        )
        _csv(
            archive,
            "trips.txt",
            [
                {
                    "trip_id": "2026-10-13:9:X:1:0800",
                    "route_id": "9",
                    "service_id": "wd",
                    "shape_id": "sh",
                    "trip_headsign": "Beta",
                    "direction_id": "0",
                },
                {
                    # after-midnight trip of the 13th: times above 24:00:00
                    "trip_id": "2026-10-13:9:X:1:2430",
                    "route_id": "9",
                    "service_id": "wd",
                    "shape_id": "sh",
                    "trip_headsign": "Beta",
                    "direction_id": "0",
                },
                {
                    "trip_id": "plain-id",
                    "route_id": "9",
                    "service_id": "once",
                    "shape_id": "sh",
                    "trip_headsign": "",
                    "direction_id": "",
                },
            ],
        )
        times = []
        for trip, start in (
            ("2026-10-13:9:X:1:0800", "08:00:00"),
            ("2026-10-13:9:X:1:2430", "24:30:00"),
            ("plain-id", "09:00:00"),
        ):
            hour, minute, _ = (int(part) for part in start.split(":"))
            end = f"{hour}:{minute + 5:02d}:00"
            times += [
                {
                    "trip_id": trip,
                    "stop_id": "a",
                    "stop_sequence": "1",
                    "arrival_time": start,
                    "departure_time": start,
                },
                {
                    "trip_id": trip,
                    "stop_id": "b",
                    "stop_sequence": "2",
                    "arrival_time": end,
                    "departure_time": end,
                },
            ]
        _csv(archive, "stop_times.txt", times)
        _csv(
            archive,
            "shapes.txt",
            [
                {
                    "shape_id": "sh",
                    "shape_pt_sequence": str(i),
                    "shape_pt_lat": lat,
                    "shape_pt_lon": "21.01",
                }
                for i, lat in ((1, "52.23"), (2, "52.22"))
            ],
        )
        _csv(
            archive,
            "calendar_dates.txt",
            [
                {"service_id": "wd", "date": "20261013", "exception_type": "1"},
                {"service_id": "wd", "date": "20261014", "exception_type": "1"},
                {"service_id": "once", "date": "20261015", "exception_type": "1"},
            ],
        )
    return path


def test_parse_keeps_service_date_headsign_and_direction(tmp_path: Path) -> None:
    schedule = data_sources.parse_schedule(_archive(tmp_path / "g.zip"))
    trips = {trip.trip_id: trip for trip in schedule.trips}

    morning = trips["2026-10-13:9:X:1:0800"]
    assert (morning.service_date, morning.headsign, morning.direction_id) == (
        date(2026, 10, 13),
        "Beta",
        0,
    )
    # Times past midnight stay on the service day they belong to, in seconds from its start.
    night = trips["2026-10-13:9:X:1:2430"]
    assert night.service_date == date(2026, 10, 13)
    assert night.departures.split(",")[0] == str(24 * 3600 + 30 * 60)
    # No date in the id: a service running on a single day still gives an unambiguous date.
    assert trips["plain-id"].service_date == date(2026, 10, 15)
    assert trips["plain-id"].direction_id is None


def test_import_stores_trip_dates_and_pattern_stops(db: Session, tmp_path: Path) -> None:
    schedule = data_sources.parse_schedule(
        _archive(tmp_path / "g.zip"), downloaded_at=datetime(2026, 10, 13, tzinfo=UTC)
    )
    data_sources.import_schedule(db, schedule)

    trip = db.get(TripSchedule, "2026-10-13:9:X:1:0800")
    assert trip is not None and trip.service_date == date(2026, 10, 13)
    assert trip.headsign == "Beta"
    rows = db.execute(
        select(PatternStop.position, PatternStop.stop_id).order_by(PatternStop.position)
    ).all()
    assert [tuple(row) for row in rows] == [(0, "a"), (1, "b")]

    # The lookup the stop panel relies on: trips calling at stop "b" on a given service day.
    found = db.scalars(
        select(TripSchedule.trip_id)
        .join(PatternStop, PatternStop.pattern_id == TripSchedule.pattern_id)
        .where(PatternStop.stop_id == "b", TripSchedule.service_date == date(2026, 10, 13))
        .order_by(TripSchedule.trip_id)
    ).all()
    assert found == ["2026-10-13:9:X:1:0800", "2026-10-13:9:X:1:2430"]


def test_migrate_upgrades_a_database_created_by_an_older_version(pg_engine: Engine) -> None:
    with pg_engine.begin() as connection:
        connection.execute(text("ALTER TABLE trip_schedules DROP COLUMN service_date"))
        connection.execute(text("ALTER TABLE trip_schedules DROP COLUMN headsign"))
        connection.execute(text("DELETE FROM schema_migrations"))

    applied = migrate(pg_engine)

    assert applied and applied[0].startswith("0001")
    with pg_engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'trip_schedules'"
                )
            ).scalars()
        )
    assert {"service_date", "headsign", "direction_id"} <= columns
    assert migrate(pg_engine) == []  # idempotent
    assert pending_migrations({path.name for path in pending_migrations(set())}) == []
