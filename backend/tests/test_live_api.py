"""Stop search, departures, lines, vehicles and predictions on a small PostGIS fixture network."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, Point
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app import api, live, predict
from app.collector import store
from app.database import get_db
from app.main import app
from app.models import (
    DelaySnapshot,
    PatternStop,
    Route,
    RouteStop,
    Stop,
    StopPattern,
    TripSchedule,
)
from app.stops import fold, natural_key
from app.timeutil import service_instant, service_seconds

WARSAW = ZoneInfo("Europe/Warsaw")
DAY = date(2026, 10, 13)
STOPS = [
    ("100101", "Łazienki", 21.000),
    ("100102", "Łazienki", 21.0001),  # second platform of the same station
    ("200101", "Rondo", 21.010),
    ("300101", "Koniec", 21.020),
    ("999901", "Łazienki", 21.300),  # a different stop with the same name, far away
]


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=WARSAW).astimezone(UTC)


def _trip(
    db: Session, trip_id: str, route: str, pattern: int, day: date, start_s: int, headsign: str
) -> None:
    stops = 3 if pattern == 0 else 2
    db.add(
        TripSchedule(
            trip_id=trip_id,
            route_id=route,
            pattern_id=pattern,
            departures=",".join(str(start_s + 600 * i) for i in range(stops)),
            service_date=day,
            headsign=headsign,
            direction_id=0,
        )
    )


@pytest.fixture
def network(db: Session) -> Session:
    db.add_all(
        [
            Route(
                id="9",
                short_name="9",
                long_name="Łazienki - Koniec",
                mode="tram",
                is_demo=False,
                shape=from_shape(LineString([(21.0, 52.2), (21.02, 52.2)]), srid=4326),
            ),
            Route(id="175", short_name="175", long_name="Bus", mode="bus", is_demo=False),
        ]
    )
    db.add_all(
        Stop(
            id=stop_id,
            name=name,
            latitude=52.2,
            longitude=lon,
            location=from_shape(Point(lon, 52.2), srid=4326),
            is_demo=False,
        )
        for stop_id, name, lon in STOPS
    )
    db.flush()
    patterns = {0: ["100101", "200101", "300101"], 1: ["100102", "300101"]}
    for pattern_id, stop_ids in patterns.items():
        db.add(StopPattern(id=pattern_id, stop_ids=",".join(stop_ids)))
    db.flush()
    for pattern_id, stop_ids in patterns.items():
        db.add_all(
            PatternStop(pattern_id=pattern_id, position=i, stop_id=s)
            for i, s in enumerate(stop_ids)
        )
    for route, stop_ids in (("9", patterns[0]), ("175", patterns[1])):
        db.add_all(
            RouteStop(route_id=route, stop_id=stop_id, sequence=i + 1)
            for i, stop_id in enumerate(stop_ids)
        )
    for hour, minute in ((12, 0), (12, 10), (12, 20), (12, 30), (12, 40), (12, 50)):
        start = (hour * 60 + minute) * 60
        _trip(db, f"2026-10-13:9:X:1:{hour}{minute:02d}", "9", 0, DAY, start, "Koniec")
    _trip(db, "2026-10-13:175:X:1:1205", "175", 1, DAY, 12 * 3600 + 300, "Koniec")
    db.commit()
    return db


@pytest.fixture
def after_midnight_trip(network: Session) -> str:
    """A trip of the 13th that runs past midnight (24:10 = 00:10 on the 14th).

    Only the test that asks for it at a fixed moment gets it: a trip pinned to a calendar date in
    the shared fixture would show up in every test that runs close to that date's midnight.
    """
    trip_id = "2026-10-13:9:X:1:2410"
    _trip(network, trip_id, "9", 0, DAY, 24 * 3600 + 600, "Koniec")
    network.commit()
    return trip_id


@pytest.fixture
def client(network: Session, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    def override() -> Iterator[Session]:
        with Session(pg_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(live, "_fallback_until", 1e18)  # never fetch the real feed in tests
    app.dependency_overrides[get_db] = override
    from app import stops

    stops._cache.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    stops._cache.clear()


def _departures(client: TestClient, stop_id: str, at: datetime, **params: object) -> dict:
    response = client.get(
        f"/api/stops/{stop_id}/departures", params={"at": at.isoformat(), **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_fold_ignores_case_and_polish_diacritics() -> None:
    assert fold("Łazienki Królewskie") == "lazienki krolewskie"
    assert fold("ŻÓŁĆ") == "zolc"


def test_natural_key_orders_lines_by_number() -> None:
    labels = ["175", "9", "N63", "10", "L1"]
    assert sorted(labels, key=natural_key) == ["9", "10", "175", "L1", "N63"]


def test_search_finds_stations_without_diacritics_and_splits_distant_namesakes(
    client: TestClient,
) -> None:
    body = client.get("/api/stops", params={"q": "lazienki"}).json()

    results = body["results"]
    assert len(results) == 2  # the 400 m rule: two platforms near each other, one far away
    near = next(item for item in results if "100101" in item["stop_ids"])
    assert near["stop_ids"] == ["100101", "100102"]
    assert near["lines"] == ["9", "175"]
    assert client.get("/api/stops", params={"q": "RON"}).json()["results"][0]["name"] == "Rondo"
    assert client.get("/api/stops", params={"q": "zzz"}).json()["results"] == []
    assert client.get("/api/stops", params={"q": ""}).status_code == 422


def test_departures_group_by_line_in_time_order_and_skip_the_terminus(client: TestClient) -> None:
    body = _departures(client, "100101", _local(DAY, 12, 3))

    assert body["data_mode"] == "live" and body["live_vehicles"] == 0
    assert {platform["id"]: platform["platform"] for platform in body["platforms"]} == {
        "100101": "01",
        "100102": "02",
    }
    by_line = {line["line"]: line for line in body["lines"]}
    # The 175 leaves at 12:05, tram 9 at 12:10 (12:00 has gone), so 175 is listed first.
    assert [line["line"] for line in body["lines"]] == ["175", "9"]
    assert [d["scheduled_at"][11:16] for d in by_line["9"]["departures"]] == [
        "10:10",
        "10:20",
        "10:30",
        "10:40",
    ]  # UTC; per_line defaults to 4
    first = by_line["9"]["departures"][0]
    assert (first["headsign"], first["platform"], first["live"]) == ("Koniec", "01", False)
    assert first["basis"] == "scheduled" and first["predicted_at"] is None
    assert first["minutes"] == 7
    assert by_line["175"]["departures"][0]["platform"] == "02"
    # The terminus has arrivals only: its line is listed but without departures.
    terminus = _departures(client, "300101", _local(DAY, 12, 3))
    assert [line["departures"] for line in terminus["lines"]] == [[], []]


def test_platform_scope_limits_the_lines_to_one_platform(client: TestClient) -> None:
    body = _departures(client, "100102", _local(DAY, 12, 3), scope="platform")

    assert [line["line"] for line in body["lines"]] == ["175"]
    assert len(body["platforms"]) == 1


def test_trips_after_midnight_belong_to_the_service_day_before(
    client: TestClient, after_midnight_trip: str
) -> None:
    # At 00:05 on the 14th the 24:10 trip of the 13th (00:10 local) is the next tram 9.
    body = _departures(client, "100101", _local(date(2026, 10, 14), 0, 5))

    departures = next(line for line in body["lines"] if line["line"] == "9")["departures"]
    assert departures[0]["trip_id"] == after_midnight_trip
    assert departures[0]["scheduled_at"] == "2026-10-13T22:10:00Z"  # 00:10 CEST
    assert departures[0]["minutes"] == 5


def test_departures_are_correct_on_the_dst_change_night(
    client: TestClient, network: Session
) -> None:
    # 25 October 2026 has 25 hours. A tram scheduled 6 h after the service-day origin (01:00
    # CEST) runs at 06:00 CET; a midnight-plus-seconds calculation would show 05:00.
    _trip(network, "2026-10-25:9:X:1:0600", "9", 0, date(2026, 10, 25), 6 * 3600, "Koniec")
    network.commit()

    body = _departures(client, "100101", _local(date(2026, 10, 25), 5, 30))

    departures = next(line for line in body["lines"] if line["line"] == "9")["departures"]
    assert departures[0]["trip_id"] == "2026-10-25:9:X:1:0600"
    assert departures[0]["scheduled_at"] == "2026-10-25T05:00:00Z"  # 06:00 CET
    assert departures[0]["minutes"] == 30


def test_departures_horizon_and_per_line_limits(client: TestClient) -> None:
    body = _departures(client, "100101", _local(DAY, 12, 3), per_line=2, horizon_minutes=20)

    tram = next(line for line in body["lines"] if line["line"] == "9")
    assert [d["scheduled_at"][11:16] for d in tram["departures"]] == ["10:10", "10:20"]
    short = _departures(client, "100101", _local(DAY, 12, 3), horizon_minutes=10)
    assert len(next(line for line in short["lines"] if line["line"] == "9")["departures"]) == 1


def _vehicle(
    trip_id: str, delay: int | None, next_stop_seq: int, now: datetime, status: int = 0
) -> dict:
    return {
        "vehicle_id": f"V/9/{next_stop_seq}",
        "observed_at": now - timedelta(seconds=20),
        "label": "3001",
        "trip_id": trip_id,
        "service_date": DAY,
        "route_id": "9",
        "line": "9",
        "mode": "tram",
        "lat": 52.2,
        "lon": 21.005,
        "bearing": 90,
        "next_stop_seq": next_stop_seq,
        "next_stop_id": "200101",
        "scheduled_s": 0,
        "delay_s": delay,
        "match_status": status,
    }


def _today_trip(network: Session, now: datetime, minutes_from_now: int, name: str) -> str:
    """A tram 9 trip of today's service day whose first stop is ``minutes_from_now`` away."""
    today = now.astimezone(WARSAW).date()
    start = int(service_seconds(today, now + timedelta(minutes=minutes_from_now)))
    trip_id = f"{today}:9:X:1:{name}"
    _trip(network, trip_id, "9", 0, today, start, "Koniec")
    network.commit()
    return trip_id


def test_live_delay_moves_the_expected_time_and_passed_trips_disappear(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    # Scheduled 2 min ago at the first stop, running 4 min late: not there yet (next stop 1).
    late = _today_trip(network, now, -2, "late")
    # Scheduled in 8 min, no live vehicle.
    plain = _today_trip(network, now, 8, "plain")
    # Scheduled in 3 min but its vehicle has already passed the first stop (next stop 2).
    passed = _today_trip(network, now, 3, "passed")
    rows = [_vehicle(late, 240, 1, now), _vehicle(passed, 0, 2, now)]
    store.upsert_latest(pg_engine, rows)

    body = client.get("/api/stops/100101/departures", params={"scope": "platform"}).json()

    tram = next(line for line in body["lines"] if line["line"] == "9")["departures"]
    trips = {d["trip_id"]: d for d in tram}
    assert passed not in trips
    assert trips[late]["live"] is True and trips[late]["delay_seconds"] == 240
    scheduled = datetime.fromisoformat(trips[late]["scheduled_at"])
    assert datetime.fromisoformat(trips[late]["predicted_at"]) == scheduled + timedelta(minutes=4)
    assert trips[late]["minutes"] in (1, 2)  # still two minutes away although "due" 2 min ago
    assert trips[plain]["live"] is False and trips[plain]["basis"] == "scheduled"
    assert body["live_vehicles"] == 1
    # The late one comes before the one scheduled later.
    assert [d["trip_id"] for d in tram].index(late) < [d["trip_id"] for d in tram].index(plain)


def test_vehicle_waiting_at_the_first_stop_still_has_that_departure(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    waiting = _today_trip(network, now, 2, "waiting")
    store.upsert_latest(pg_engine, [_vehicle(waiting, None, 1, now, status=4)])

    body = client.get("/api/stops/100101/departures", params={"scope": "platform"}).json()

    tram = next(line for line in body["lines"] if line["line"] == "9")["departures"]
    entry = next(d for d in tram if d["trip_id"] == waiting)
    assert entry["live"] is False and entry["vehicle_id"] == "V/9/1"


def test_stale_vehicle_delay_is_ignored(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, 5, "stale")
    row = _vehicle(trip, 300, 1, now)
    row["observed_at"] = now - timedelta(minutes=4)
    store.upsert_latest(pg_engine, [row])

    body = client.get("/api/stops/100101/departures", params={"scope": "platform"}).json()

    entry = next(d for line in body["lines"] for d in line["departures"] if d["trip_id"] == trip)
    assert entry["live"] is False and entry["delay_seconds"] is None


def test_unknown_stop_is_404_and_old_import_is_a_clear_503(
    client: TestClient, network: Session
) -> None:
    assert client.get("/api/stops/nope/departures").status_code == 404

    network.execute(TripSchedule.__table__.update().values(service_date=None))
    network.commit()
    response = client.get("/api/stops/100101/departures")
    assert response.status_code == 503 and "import_gtfs" in response.json()["detail"]


def test_line_detail_returns_shape_stops_and_resolves_by_number(client: TestClient) -> None:
    body = client.get("/api/lines/9").json()

    assert (body["line"], body["route_id"], body["mode"]) == ("9", "9", "tram")
    assert body["shape"]["type"] == "LineString"
    assert [stop["id"] for stop in body["stops"]] == ["100101", "200101", "300101"]
    assert client.get("/api/lines/nope").status_code == 404


def test_line_vehicles_only_contains_that_line(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    other = _vehicle("t", 10, 1, now) | {"vehicle_id": "V/175/1", "line": "175", "mode": "bus"}
    store.upsert_latest(pg_engine, [_vehicle("t", 100, 1, now), other])

    tram = client.get("/api/lines/9/vehicles").json()
    everything = client.get("/api/vehicles").json()

    assert [f["id"] for f in tram["features"]] == ["V/9/1"]
    assert tram["features"][0]["properties"]["delay_seconds"] == 100
    assert {f["id"] for f in everything["features"]} == {"V/9/1", "V/175/1"}
    assert everything["data_mode"] == "live"


def test_vehicle_detail_lists_next_stops_with_expected_times(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -3, "detail")
    store.upsert_latest(pg_engine, [_vehicle(trip, 90, 2, now)])

    body = client.get("/api/vehicles/V/9/2").json()  # ids contain slashes

    assert (body["line"], body["delay_seconds"], body["status"]) == ("9", 90, "ok")
    assert body["headsign"] == "Koniec"
    assert [stop["name"] for stop in body["next_stops"]] == ["Rondo", "Koniec"]
    first = body["next_stops"][0]
    assert first["delay_seconds"] == 90
    assert datetime.fromisoformat(first["predicted_at"]) - datetime.fromisoformat(
        first["scheduled_at"]
    ) == timedelta(seconds=90)
    assert body["model"] == {"name": "persistence", "version": "1", "kind": "baseline"}
    assert client.get("/api/vehicles/V/9/404").status_code == 404


def test_predictions_contract_for_a_stop_and_a_trip(
    client: TestClient, network: Session, pg_engine: Engine
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "pred")
    store.upsert_latest(pg_engine, [_vehicle(trip, 120, 1, now)])

    by_stop = client.get("/api/predictions", params={"stop_id": "100101"}).json()
    by_trip = client.get("/api/predictions", params={"trip_id": trip}).json()

    assert by_stop["model"]["kind"] == "baseline"
    live_item = next(item for item in by_stop["predictions"] if item["trip_id"] == trip)
    assert live_item["predicted_delay_s"] == 120 and live_item["basis"] == "live_delay"
    assert live_item["lower_delay_s"] is None and live_item["upper_delay_s"] is None
    assert [item["stop_id"] for item in by_trip["predictions"]] == ["100101", "200101", "300101"]
    assert all(item["predicted_delay_s"] == 120 for item in by_trip["predictions"])
    assert client.get("/api/predictions").status_code == 422
    assert (
        client.get("/api/predictions", params={"stop_id": "1", "trip_id": "2"}).status_code == 422
    )
    assert client.get("/api/predictions", params={"trip_id": "nope"}).status_code == 404


def test_a_configured_predictor_replaces_the_baseline(
    client: TestClient, network: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _today_trip(network, datetime.now(UTC), 5, "model")

    class Fixed:
        name, version, kind = "fixed", "9", "model"

        def predict(self, rows, explain=False, lang="en"):
            return [predict.Prediction(60, -30, 150, "live_delay") for _ in rows]

    monkeypatch.setattr(predict, "_predictor", Fixed())

    body = client.get("/api/predictions", params={"stop_id": "100101"}).json()

    assert body["model"] == {"name": "fixed", "version": "9", "kind": "model"}
    got = {
        (p["predicted_delay_s"], p["lower_delay_s"], p["upper_delay_s"])
        for p in body["predictions"]
    }
    assert got == {(60, -30, 150)}


def test_baseline_predictor_persists_the_current_delay() -> None:
    from app.features import FeatureRow

    def row(delay: int | None) -> FeatureRow:
        return FeatureRow("t", "9", "s", 3, datetime.now(UTC), 0, delay, None, None, 12, 1, None, 0)

    one, two = predict.BaselinePredictor().predict([row(95), row(None)])

    assert (one.predicted_delay_s, one.basis) == (95, "live_delay")
    assert (two.predicted_delay_s, two.basis) == (0, "scheduled")


def test_service_instant_used_by_the_api_matches_wall_clock_on_a_normal_day() -> None:
    assert service_instant(DAY, 12 * 3600) == _local(DAY, 12)


def test_a_running_collector_stops_the_api_from_fetching_the_feed(
    client: TestClient, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The upstream feed is stale (no recent positions), but the collector is alive: the API must
    # not start polling the feed as well.
    store.set_state(pg_engine, store.HEARTBEAT_KEY, datetime.now(UTC).isoformat())
    monkeypatch.setattr(live, "_fallback_until", 0.0)

    def no_fetch(*args: object, **kwargs: object) -> None:
        raise AssertionError("the API fetched the upstream feed")

    monkeypatch.setattr(live, "GtfsRtClient", no_fetch)

    assert client.get("/api/vehicles").status_code == 200


def test_network_marks_each_stop_with_the_modes_that_serve_it(client: TestClient) -> None:
    response = client.get("/api/network")
    assert response.status_code == 200
    modes = {
        feature["id"]: feature["properties"]["mode"]
        for feature in response.json()["features"]
        if feature["properties"]["entity_type"] == "stop"
    }
    # Tram 9 calls at 100101 and 200101, bus 175 at 100102; both call at 300101.
    assert modes["100101"] == "tram"
    assert modes["200101"] == "tram"
    assert modes["100102"] == "bus"
    assert modes["300101"] == "both"
    assert modes["999901"] == "bus"  # no route calls there: falls back to bus


def _snapshots(
    db: Session, now: datetime, minutes: int, lines: tuple[str, ...], vehicles: int = 4
) -> None:
    minute = now.replace(second=0, microsecond=0)
    db.add_all(
        DelaySnapshot(
            observed_at=minute - timedelta(minutes=age),
            line=line,
            mode="bus",
            vehicles=vehicles,
            mean_delay_seconds=120,
            median_delay_seconds=90,
            early=1,
            on_time=2,
            late=1,
        )
        for age in range(minutes)
        for line in lines
    )
    db.commit()


@pytest.fixture
def punctuality_client(client: TestClient) -> Iterator[TestClient]:
    api._punctuality_cache.clear()
    yield client
    api._punctuality_cache.clear()


def test_punctuality_window_is_capped_and_long_windows_are_bucketed(
    punctuality_client: TestClient, db: Session
) -> None:
    _snapshots(db, datetime.now(UTC), 360, ("9", "175"))
    assert punctuality_client.get("/api/punctuality?minutes=1440").status_code == 422

    short = punctuality_client.get("/api/punctuality?minutes=30").json()
    long = punctuality_client.get("/api/punctuality?minutes=360").json()

    by_line = {line["line"]: line for line in short["lines"]}
    assert len(by_line["9"]["series"]) in (30, 31)  # one point per stored minute
    assert by_line["9"]["mean_delay_minutes"] == 2.0
    long_line = {line["line"]: line for line in long["lines"]}["9"]
    assert 70 <= len(long_line["series"]) <= 73  # five-minute steps
    assert long_line["observations"] == 4 * 360
    assert long_line["series"][1]["vehicles"] == 4
    assert long_line["on_time_share"] == 0.5
    assert long["snapshots"] == 360


def test_punctuality_answer_is_cached_for_a_minute(
    punctuality_client: TestClient, db: Session
) -> None:
    now = datetime.now(UTC)
    _snapshots(db, now, 20, ("9",))
    first = punctuality_client.get("/api/punctuality?minutes=60").json()
    _snapshots(db, now - timedelta(minutes=30), 5, ("175",))
    again = punctuality_client.get("/api/punctuality?minutes=60").json()
    assert again == first  # served from the cache
    api._punctuality_cache.clear()
    fresh = punctuality_client.get("/api/punctuality?minutes=60").json()
    assert {line["line"] for line in fresh["lines"]} == {"9", "175"}


@pytest.mark.parametrize(
    "at",
    [
        "9999-12-31T23:59:59Z",
        "0001-01-01T00:00:00Z",
        "1999-12-31T23:59:59Z",
        "2100-01-01T00:00:01Z",
    ],
)
def test_an_absurd_at_is_a_422_not_a_500(client: TestClient, at: str) -> None:
    response = client.get("/api/stops/100101/departures", params={"at": at})
    assert response.status_code == 422


def test_only_the_departures_that_can_be_shown_are_predicted(
    client: TestClient, network: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    class Counting:
        name, version, kind = "counting", "1", "baseline"

        def predict(self, rows, explain=False, lang="en"):
            seen.append(len(rows))
            return [predict.Prediction(0) for _ in rows]

    monkeypatch.setattr(predict, "_predictor", Counting())
    body = _departures(client, "100101", _local(DAY, 12, 0), per_line=1, horizon_minutes=360)

    tram = next(line for line in body["lines"] if line["line"] == "9")
    assert [d["scheduled_at"][11:16] for d in tram["departures"]] == ["10:00"]
    # the station has 7 trips in the next six hours; with per_line=1 the line of 6 trips is cut
    # to 1 + the slack, and the single trip of the other line stays: 4 + 1 rows are predicted
    assert sum(seen) == 5
