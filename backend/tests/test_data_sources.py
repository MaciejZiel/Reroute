"""Tests for compact GTFS imports and live vehicle feed handling."""

import csv
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.transit import gtfs_realtime_pb2

from app import data_sources


def _write_csv(archive: zipfile.ZipFile, name: str, rows: list[dict[str, str]]) -> None:
    columns = list(rows[0])
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(name, buffer.getvalue())


@pytest.fixture
def gtfs_archive(tmp_path: Path) -> Path:
    archive_path = tmp_path / "tiny-warsaw.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_csv(
            archive,
            "routes.txt",
            [
                {
                    "route_id": "bus-175",
                    "route_short_name": "175",
                    "route_long_name": "Airport",
                    "route_type": "3",
                },
                {
                    "route_id": "tram-9",
                    "route_short_name": "9",
                    "route_long_name": "Central route",
                    "route_type": "0",
                },
                {
                    "route_id": "rail-1",
                    "route_short_name": "R1",
                    "route_long_name": "Train",
                    "route_type": "2",
                },
            ],
        )
        _write_csv(
            archive,
            "stops.txt",
            [
                {"stop_id": "s1", "stop_name": "Centrum", "stop_lat": "52.23", "stop_lon": "21.01"},
                {
                    "stop_id": "s2",
                    "stop_name": "Politechnika",
                    "stop_lat": "52.22",
                    "stop_lon": "21.01",
                },
                {
                    "stop_id": "s3",
                    "stop_name": "Pole Mokotowskie",
                    "stop_lat": "52.21",
                    "stop_lon": "21.00",
                },
            ],
        )
        _write_csv(
            archive,
            "trips.txt",
            [
                {"trip_id": "b1", "route_id": "bus-175", "shape_id": "bus-shape"},
                {"trip_id": "b2", "route_id": "bus-175", "shape_id": "bus-shape"},
                {"trip_id": "t1", "route_id": "tram-9", "shape_id": "tram-shape"},
                {"trip_id": "r1", "route_id": "rail-1", "shape_id": "rail-shape"},
            ],
        )
        _write_csv(
            archive,
            "stop_times.txt",
            [
                {"trip_id": "b1", "stop_id": "s1", "stop_sequence": "1"},
                {"trip_id": "b1", "stop_id": "s2", "stop_sequence": "2"},
                {"trip_id": "t1", "stop_id": "s2", "stop_sequence": "1"},
                {"trip_id": "t1", "stop_id": "s3", "stop_sequence": "2"},
            ],
        )
        _write_csv(
            archive,
            "shapes.txt",
            [
                {
                    "shape_id": "bus-shape",
                    "shape_pt_sequence": "1",
                    "shape_pt_lat": "52.23",
                    "shape_pt_lon": "21.01",
                },
                {
                    "shape_id": "bus-shape",
                    "shape_pt_sequence": "2",
                    "shape_pt_lat": "52.22",
                    "shape_pt_lon": "21.01",
                },
                {
                    "shape_id": "tram-shape",
                    "shape_pt_sequence": "1",
                    "shape_pt_lat": "52.22",
                    "shape_pt_lon": "21.01",
                },
                {
                    "shape_id": "tram-shape",
                    "shape_pt_sequence": "2",
                    "shape_pt_lat": "52.21",
                    "shape_pt_lon": "21.00",
                },
            ],
        )
    return archive_path


def test_parse_schedule_keeps_bus_and_tram_shapes_and_connections(gtfs_archive: Path) -> None:
    result = data_sources.parse_schedule(gtfs_archive)

    assert [(route.id, route.mode) for route in result.routes] == [
        ("bus-175", "bus"),
        ("tram-9", "tram"),
    ]
    assert len(result.stops) == 3
    assert {(item.route_id, item.stop_id) for item in result.route_stops} == {
        ("bus-175", "s1"),
        ("bus-175", "s2"),
        ("tram-9", "s2"),
        ("tram-9", "s3"),
    }
    assert all(route.shapes and len(route.shapes[0]) == 2 for route in result.routes)


def test_route_mode_supports_standard_and_extended_gtfs_types() -> None:
    assert data_sources._route_mode("0") == "tram"
    assert data_sources._route_mode("900") == "tram"
    assert data_sources._route_mode("3") == "bus"
    assert data_sources._route_mode("700") == "bus"
    assert data_sources._route_mode("2") is None


def test_parse_schedule_rejects_an_incomplete_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("routes.txt", "route_id,route_type\n")

    with pytest.raises(ValueError, match="missing required files"):
        data_sources.parse_schedule(archive_path)


def test_live_vehicle_feed_maps_route_and_ignores_out_of_area_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(datetime.now(UTC).timestamp())
    entity = feed.entity.add()
    entity.id = "vehicle-1"
    entity.vehicle.vehicle.id = "V/9/3"
    entity.vehicle.vehicle.label = "fleet-9"
    entity.vehicle.position.latitude = 52.23
    entity.vehicle.position.longitude = 21.01
    entity.vehicle.timestamp = feed.header.timestamp
    outside = feed.entity.add()
    outside.id = "vehicle-outside"
    outside.vehicle.position.latitude = 50.0
    outside.vehicle.position.longitude = 19.0

    class FakeResponse:
        content = feed.SerializeToString()

        @staticmethod
        def raise_for_status() -> None:
            return None

    class FakeSession:
        @staticmethod
        def scalars(_query: object) -> SimpleNamespace:
            return SimpleNamespace(
                all=lambda: [SimpleNamespace(id="tram-9", short_name="9", mode="tram")]
            )

    monkeypatch.setattr(data_sources.httpx, "get", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(data_sources, "_vehicle_cache_until", 0)
    monkeypatch.setattr(data_sources, "_vehicle_cache", data_sources.LiveVehicleSnapshot((), None))

    snapshot = data_sources.get_live_vehicles(FakeSession())  # type: ignore[arg-type]

    assert len(snapshot.vehicles) == 1
    vehicle = snapshot.vehicles[0]
    assert vehicle.id == "V/9/3"
    assert vehicle.line == "9"
    assert vehicle.mode == "tram"
    assert vehicle.is_stale is False


def test_live_feed_failure_leaves_a_clear_demo_fallback_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request(*_args: object, **_kwargs: object) -> None:
        raise data_sources.httpx.ConnectError("feed offline")

    monkeypatch.setattr(data_sources.httpx, "get", fail_request)
    monkeypatch.setattr(data_sources, "_vehicle_cache_until", 0)
    monkeypatch.setattr(data_sources, "_vehicle_cache", data_sources.LiveVehicleSnapshot((), None))

    snapshot = data_sources.get_live_vehicles(SimpleNamespace())  # type: ignore[arg-type]

    assert snapshot.vehicles == ()
    assert snapshot.error == "feed offline"


def test_parse_schedule_times_segments_on_the_busiest_service_day(tmp_path: Path) -> None:
    archive_path = tmp_path / "timed.zip"
    stop = {"stop_lat": "52.23", "stop_lon": "21.01"}
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_csv(
            archive,
            "routes.txt",
            [{"route_id": "9", "route_short_name": "9", "route_long_name": "", "route_type": "0"}],
        )
        _write_csv(
            archive,
            "stops.txt",
            [{"stop_id": stop_id, "stop_name": stop_id, **stop} for stop_id in ("a", "b", "c")],
        )
        _write_csv(
            archive,
            "calendar_dates.txt",
            [
                {"date": "20261012", "service_id": "weekday", "exception_type": "1"},
                {"date": "20261013", "service_id": "weekday", "exception_type": "1"},
                {"date": "20261011", "service_id": "sunday", "exception_type": "1"},
            ],
        )
        _write_csv(
            archive,
            "trips.txt",
            [
                {"trip_id": "w1", "route_id": "9", "service_id": "weekday", "shape_id": ""},
                {"trip_id": "w2", "route_id": "9", "service_id": "weekday", "shape_id": ""},
                {"trip_id": "s1", "route_id": "9", "service_id": "sunday", "shape_id": ""},
            ],
        )
        times = {
            "w1": ["06:00:00", "06:02:00", "06:05:00"],
            "w2": ["24:10:00", "24:13:00", "24:16:00"],
            "s1": ["07:00:00", "07:10:00", "07:20:00"],
        }
        _write_csv(
            archive,
            "stop_times.txt",
            [
                {
                    "trip_id": trip_id,
                    "stop_id": stop_id,
                    "stop_sequence": str(sequence),
                    "arrival_time": clock[sequence],
                    "departure_time": clock[sequence],
                }
                for trip_id, clock in times.items()
                for sequence, stop_id in enumerate(("a", "b", "c"))
            ],
        )
        archive.writestr("shapes.txt", "shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon\n")

    result = data_sources.parse_schedule(archive_path)

    assert result.service_date == "2026-10-12"
    segments = {(item.from_stop_id, item.to_stop_id): item for item in result.segments}
    assert set(segments) == {("a", "b"), ("b", "c")}
    # Only the two weekday trips count; the slow Sunday trip is ignored.
    assert segments[("a", "b")].trips == 2
    assert segments[("a", "b")].run_seconds == 150
    assert segments[("b", "c")].run_seconds == 180
    # First departure 06:00, last 24:10 (after midnight on the same service day).
    assert segments[("a", "b")].service_minutes == 18 * 60 + 10
