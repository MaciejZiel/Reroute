"""Delays derived from a trimmed recording of the real Warsaw vehicle feed.

The fixtures are a short recording of the Warsaw GTFS-Realtime vehicle feed (Miasto Stołeczne
Warszawa, distributed by Mikołaj Kuranowski, https://mkuran.pl/gtfs/) and the timetables of the
same trips from the ZTM Warsaw GTFS schedule.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from gtfs_rt_kit import parse_feed

from app.api import _line_punctuality
from app.collector.derive import MatchStatus, TripTimetable, derive_poll
from app.collector.service import to_observations
from app.demo_data import demo_line_delays
from app.timeutil import trip_service_date

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def recorded():
    trips = json.loads((FIXTURES / "warsaw_trips_sample.json").read_text())["trips"]
    feed = parse_feed((FIXTURES / "warsaw_vehicles_sample.pb").read_bytes())
    timetables = {
        trip_id: TripTimetable(
            trip_id=trip_id,
            route_id=trip["route_id"],
            line=trip["line"],
            mode=trip["mode"],
            service_date=trip_service_date(trip_id),
            stop_ids=tuple(stop[0] for stop in trip["stops"]),
            latitudes=tuple(stop[1] for stop in trip["stops"]),
            longitudes=tuple(stop[2] for stop in trip["stops"]),
            departures=tuple(stop[3] for stop in trip["stops"]),
        )
        for trip_id, trip in trips.items()
    }
    return feed, timetables


def test_recorded_feed_delays_match_the_timetable(recorded) -> None:
    feed, timetables = recorded
    observations = to_observations(feed)

    result = derive_poll(observations, lambda ids: timetables, {}, feed.timestamp)

    rows = {row["vehicle_id"]: row for row in result.observations}
    assert len(observations) == 12
    # Two vehicles are not on their trip's route (more than 250 m away): no delay is guessed.
    unscored = {v for v, row in rows.items() if row["delay_s"] is None}
    assert unscored == {"V/511/3", "V/507/2"}
    assert all(rows[v]["match_status"] == MatchStatus.OFF_ROUTE for v in unscored)
    assert {v: row["delay_s"] for v, row in rows.items() if row["delay_s"] is not None} == {
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
    assert rows["V/9/9"]["mode"] == "tram"
    assert rows["V/N63/1"]["line"] == "N63"


def test_vehicles_outside_warsaw_are_dropped(recorded) -> None:
    feed, _ = recorded
    assert all(20.3 <= obs.longitude <= 21.8 for obs in to_observations(feed))


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
