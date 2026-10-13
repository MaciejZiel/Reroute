"""Tests for compact GTFS imports and live vehicle feed handling."""

import csv
import zipfile
from pathlib import Path

import pytest

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
