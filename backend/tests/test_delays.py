"""Delay estimation from live positions, checked against a recorded Warsaw feed sample.

The fixtures are a trimmed recording of the Warsaw GTFS-Realtime vehicle feed (Miasto
Stołeczne Warszawa, distributed by Mikołaj Kuranowski, https://mkuran.pl/gtfs/) and the
timetables of the same trips from the ZTM Warsaw GTFS schedule.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import data_sources
from app.api import _line_punctuality
from app.delays import (
    TIMEZONE,
    ScheduledStop,
    VehicleDelay,
    delay_seconds,
    demo_line_delays,
    estimate_delays,
    scheduled_seconds_at,
    summarise,
)

FIXTURES = Path(__file__).parent / "fixtures"
STOPS = [
    ScheduledStop(52.2300, 21.0100, 8 * 3600),
    ScheduledStop(52.2300, 21.0200, 8 * 3600 + 240),
    ScheduledStop(52.2400, 21.0200, 8 * 3600 + 600),
]


def _local(hour: int, minute: int, day: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TIMEZONE).astimezone(UTC)


def test_position_between_stops_interpolates_the_timetable() -> None:
    halfway = scheduled_seconds_at(52.2300, 21.0150, STOPS)

    assert halfway == pytest.approx(8 * 3600 + 120, abs=2)
    assert delay_seconds(_local(8, 2), halfway) == pytest.approx(0, abs=2)
    assert delay_seconds(_local(8, 5), halfway) == pytest.approx(180, abs=2)


def test_vehicles_off_route_or_waiting_at_the_first_stop_are_skipped() -> None:
    assert scheduled_seconds_at(52.2500, 21.0500, STOPS) is None
    assert scheduled_seconds_at(52.2300, 21.0090, STOPS) is None


def test_trips_after_midnight_belong_to_the_previous_service_day() -> None:
    night = [ScheduledStop(52.23, 21.01, 24 * 3600 + 1800), ScheduledStop(52.23, 21.02, 25 * 3600)]
    scheduled = scheduled_seconds_at(52.23, 21.015, night)

    # 00:45 on 13 October is 24:45 on the 12 October service day.
    assert delay_seconds(_local(0, 47, day=13), scheduled) == pytest.approx(120, abs=2)


def test_implausible_delays_are_discarded() -> None:
    assert delay_seconds(_local(10, 0), 8 * 3600) is None


def test_summary_counts_early_on_time_and_late_vehicles() -> None:
    delays = [
        VehicleDelay("a", "9", "tram", -120),
        VehicleDelay("b", "9", "tram", 30),
        VehicleDelay("c", "9", "tram", 179),
        VehicleDelay("d", "9", "tram", 420),
        VehicleDelay("e", "175", "bus", 60),
    ]

    tram, bus = summarise(delays)

    assert (tram.line, tram.vehicles, tram.early, tram.on_time, tram.late) == ("9", 4, 1, 2, 1)
    assert tram.median_delay_seconds == 104
    assert (bus.line, bus.on_time) == ("175", 1)


@pytest.fixture
def recorded_vehicles(monkeypatch: pytest.MonkeyPatch) -> tuple:
    trips = json.loads((FIXTURES / "warsaw_trips_sample.json").read_text())["trips"]
    content = (FIXTURES / "warsaw_vehicles_sample.pb").read_bytes()

    class FakeResponse:
        def __init__(self) -> None:
            self.content = content

        @staticmethod
        def raise_for_status() -> None:
            return None

    routes = [
        SimpleNamespace(id=trip["route_id"], short_name=trip["line"], mode=trip["mode"])
        for trip in trips.values()
    ]
    session = SimpleNamespace(scalars=lambda _query: SimpleNamespace(all=lambda: routes))
    monkeypatch.setattr(data_sources.httpx, "get", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(data_sources, "_vehicle_cache_until", 0)
    snapshot = data_sources.get_live_vehicles(session)  # type: ignore[arg-type]
    schedules = {
        trip_id: [ScheduledStop(lat, lon, departure) for _, lat, lon, departure in trip["stops"]]
        for trip_id, trip in trips.items()
    }
    return snapshot, schedules


def test_recorded_feed_delays_match_the_timetable(recorded_vehicles: tuple) -> None:
    snapshot, schedules = recorded_vehicles

    delays = {item.vehicle_id: item for item in estimate_delays(snapshot.vehicles, schedules)}

    assert len(snapshot.vehicles) == 12
    assert all(vehicle.trip_id in schedules for vehicle in snapshot.vehicles)
    # Two vehicles stand at the first stop of their trip (layover) and are not scored.
    assert len(delays) == 10
    assert "V/511/3" not in delays and "V/507/2" not in delays
    assert {vehicle_id: item.delay_seconds for vehicle_id, item in delays.items()} == {
        "V/N63/1": 211,
        "V/N62/227": -105,
        "V/N46/186": -45,
        "V/N22/190": 369,
        "V/N42/218": 337,
        "V/N14/131": 172,
        "V/N02/2": -5,
        "V/N34/259": 115,
        "V/9/9": -66,
        "V/N02/1": 135,
    }
    assert delays["V/9/9"].mode == "tram"

    lines = {line.line: line for line in summarise(delays.values())}
    assert lines["N02"].vehicles == 2
    assert lines["N22"].late == 1
    assert lines["N62"].early == 1


def test_line_punctuality_weights_snapshots_by_vehicles() -> None:
    now = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    rows = demo_line_delays(now, [("9", "tram"), ("175", "bus")], points=4)

    lines = _line_punctuality(rows)

    observations = [line.observations for line in lines]
    assert observations == sorted(observations, reverse=True)
    assert sum(observations) == sum(row.vehicles for row in rows)
    for line in lines:
        assert len(line.series) == 4
        assert line.early_share + line.on_time_share + line.late_share == pytest.approx(1, 0.01)
        assert line.series[-1].observed_at == now.replace(microsecond=0)
    assert rows[0].observed_at == now - timedelta(minutes=7.5)
