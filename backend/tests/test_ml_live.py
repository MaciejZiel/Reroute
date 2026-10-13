"""The model's live inputs equal its training inputs, and the API serves model, reasons and
interval; without a promoted model the baseline answers."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from test_live_api import _today_trip, _vehicle, client, network  # noqa: E402, F401

from app import predict  # noqa: E402
from app.collector import store  # noqa: E402
from app.collector.derive import PollResult  # noqa: E402
from app.ml.featurize import Event, TargetStop  # noqa: E402
from app.ml.live import load_index, model_inputs  # noqa: E402

DAY = date(2026, 10, 13)
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def _row(trip: str, seq: int, delay: int, minute: float) -> dict:
    scheduled = T0 + timedelta(minutes=minute)
    return {
        "service_date": DAY,
        "trip_id": f"2026-10-13:9:X:{trip}",
        "stop_seq": seq,
        "stop_id": f"s{seq}",
        "line": "9",
        "scheduled_at": scheduled,
        "actual_at": scheduled + timedelta(seconds=delay),
        "delay_s": delay,
        "gap_s": 30,
    }


def _events(rows: list[dict]) -> list[Event]:
    return [
        Event(
            r["trip_id"],
            r["stop_seq"],
            r["stop_id"],
            r["line"],
            r["scheduled_at"].timestamp(),
            r["actual_at"].timestamp(),
            r["delay_s"],
            r["actual_at"].timestamp() + r["gap_s"],
        )
        for r in rows
    ]


def test_the_model_does_not_answer_without_a_fresh_vehicle_or_a_recent_last_stop(
    db: Session, pg_engine: Engine
) -> None:
    rows = [_row("a", seq, 60, 3 * seq) for seq in range(1, 6)]
    store.write_poll(pg_engine, PollResult(arrivals=rows))
    last_known = max(r["actual_at"] for r in rows) + timedelta(seconds=30)
    trip = rows[0]["trip_id"]
    target = TargetStop(trip, 9, "s9", "9", (T0 + timedelta(minutes=40)).timestamp())

    def inputs(now: datetime, delay: float | None, age: float | None):
        index = load_index(db, [trip], ["9"], ["s9"], now)
        return model_inputs(index, [target], now, [delay], [age])[0]

    assert inputs(last_known + timedelta(seconds=20), 60, 5) is not None
    assert inputs(last_known + timedelta(seconds=20), None, None) is None  # no live vehicle
    assert inputs(last_known + timedelta(seconds=20), 60, 300) is None  # vehicle report too old
    assert inputs(last_known + timedelta(seconds=20), 60, 100) is not None  # late, still used
    # the 50-minute-old trip of the review: the last recorded stop is far too old
    assert inputs(last_known + timedelta(minutes=50), 60, 5) is None


@pytest.fixture(scope="module")
def model_file() -> str:
    """A small complete model file (synthetic, committed): no training library is needed."""
    return str(Path(__file__).parent / "fixtures" / "ml_tiny_delay_model.json")


def _arrival(trip: str, seq: int, stop: str, delay: int, now: datetime) -> dict:
    actual = now - timedelta(seconds=40)
    return {
        "service_date": now.date(),
        "trip_id": trip,
        "stop_seq": seq,
        "stop_id": stop,
        "line": "9",
        "scheduled_at": actual - timedelta(seconds=delay),
        "actual_at": actual,
        "delay_s": delay,
        "gap_s": 10,
    }


def test_the_api_serves_the_model_with_an_interval_and_reasons(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlx")
    store.write_poll(pg_engine, PollResult(arrivals=[_arrival(trip, 1, "100101", 150, now)]))
    store.upsert_latest(pg_engine, [_vehicle(trip, 150, 2, now)])

    body = client.get("/api/predictions", params={"trip_id": trip}).json()

    assert body["model"]["kind"] == "model" and body["model"]["name"] == "gbt-delay"
    first = body["predictions"][0]
    assert first["stop_id"] == "200101" and first["basis"] == "live_delay"
    assert first["lower_delay_s"] <= first["predicted_delay_s"] <= first["upper_delay_s"]
    assert first["why"]["summary"].startswith("Now")
    assert all(isinstance(f["effect_seconds"], int) and f["text"] for f in first["why"]["factors"])

    vehicle = client.get("/api/vehicles/V/9/2").json()
    assert vehicle["model"]["kind"] == "model"
    assert vehicle["next_stops"][0]["why"]["summary"]
    assert vehicle["next_stops"][0]["lower_delay_seconds"] is not None


def test_a_trip_without_recorded_stops_falls_back_to_persistence(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlnone")
    store.upsert_latest(pg_engine, [_vehicle(trip, 77, 1, now)])

    body = client.get("/api/predictions", params={"trip_id": trip}).json()

    assert {p["predicted_delay_s"] for p in body["predictions"]} == {77}
    assert all(p["why"] is None and p["lower_delay_s"] is None for p in body["predictions"])


def test_a_model_that_did_not_beat_the_baseline_is_not_served_by_default(
    model_file: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = json.loads(open(model_file).read())
    document["card"]["promoted"] = False
    shelved = tmp_path / "delay_model.json"
    shelved.write_text(json.dumps(document))
    monkeypatch.setenv("DELAY_MODEL_FILE", str(shelved))

    assert predict.get_predictor().kind == "baseline"

    monkeypatch.setattr(predict, "_predictor", None)
    monkeypatch.setenv("PREDICTOR", "model")  # an explicit choice overrides the card
    assert predict.get_predictor().kind == "model"

    monkeypatch.setattr(predict, "_predictor", None)
    monkeypatch.delenv("PREDICTOR")
    shelved.write_text("{broken")
    assert predict.get_predictor().kind == "baseline"


def test_the_arrival_time_index_exists(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_stop_arrival_events_actual_at'"
            )
        ).scalar()
    assert found and "brin" in found.lower()


def test_a_trip_last_recorded_long_ago_with_no_vehicle_stays_scheduled_not_live(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, 8, "mlold")
    old = _arrival(trip, 1, "100101", 300, now - timedelta(minutes=50))
    store.write_poll(pg_engine, PollResult(arrivals=[old]))  # recorded 50 minutes ago, 5 min late

    body = client.get("/api/predictions", params={"trip_id": trip}).json()
    board = client.get("/api/stops/100101/departures").json()

    assert body["model"]["kind"] == "model"
    assert {p["basis"] for p in body["predictions"]} == {"scheduled"}
    assert all(p["predicted_delay_s"] == 0 and p["why"] is None for p in body["predictions"])
    assert all(p["lower_delay_s"] is None for p in body["predictions"])
    departures = [d for line in board["lines"] for d in line["departures"] if d["trip_id"] == trip]
    assert departures and not any(d["live"] for d in departures)


def test_a_vehicle_with_a_stale_last_stop_gets_the_baseline_answer(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlstale")
    old = _arrival(trip, 1, "100101", 300, now - timedelta(minutes=50))
    store.write_poll(pg_engine, PollResult(arrivals=[old]))
    store.upsert_latest(pg_engine, [_vehicle(trip, 77, 2, now)])

    body = client.get("/api/predictions", params={"trip_id": trip}).json()

    for item in body["predictions"]:
        assert item["predicted_delay_s"] == 77 and item["basis"] == "live_delay"
        assert item["lower_delay_s"] is None and item["why"] is None  # plain persistence


def test_the_model_stays_silent_beyond_the_stops_it_was_trained_for(model_file: str) -> None:
    from app.features import FeatureRow
    from app.ml.model import DelayModel, ModelPredictor

    predictor = ModelPredictor(DelayModel.load(model_file))
    base = {name: 0.0 for name in predictor.model.feature_names}
    base["origin_delay_s"] = base["vehicle_delay_s"] = 100.0

    def row(stops_ahead: float) -> FeatureRow:
        inputs = {**base, "stops_ahead": stops_ahead, "sched_run_s": 60 * stops_ahead}
        return FeatureRow(
            "t", "9", "s", 3, datetime.now(UTC), 0, 100, None, None, 12, 1, None, 0, inputs
        )

    near, far = predictor.predict([row(12), row(13)])

    assert near.lower_delay_s is not None
    assert far.lower_delay_s is None and far.predicted_delay_s == 100  # persistence, no interval


def test_a_model_file_with_unknown_features_or_without_a_range_is_refused(
    model_file: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml.model import DelayModel

    document = json.loads(open(model_file).read())
    document["feature_names"][0] = "weather_index"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="cannot compute"):
        DelayModel.load(path)

    # PREDICTOR=model with a broken file serves the baseline instead of failing requests.
    monkeypatch.setenv("DELAY_MODEL_FILE", str(path))
    monkeypatch.setenv("PREDICTOR", "model")
    assert predict.get_predictor().kind == "baseline"

    document = json.loads(open(model_file).read())
    del document["serving_range"]
    model = DelayModel(document)
    assert not model.in_range({"stops_ahead": 1.0})


@pytest.fixture
def shap_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from app.ml.ensemble import Ensemble

    calls: list[int] = []
    original = Ensemble.contributions

    def counting(self, x):
        calls.append(1)
        return original(self, x)

    monkeypatch.setattr(Ensemble, "contributions", counting)
    return calls


def _two_live_trips(
    network: Session,  # noqa: F811
    pg_engine: Engine,
    now: datetime,
) -> tuple[str, str]:
    # B departs before A: the first returned entry is B whichever order the trips were found in.
    a = _today_trip(network, now, 9, "mlA")
    b = _today_trip(network, now, 6, "mlB")
    for trip in (a, b):
        store.write_poll(pg_engine, PollResult(arrivals=[_arrival(trip, 1, "100101", 120, now)]))
    store.upsert_latest(pg_engine, [_vehicle(a, 120, 1, now), _vehicle(b, 90, 2, now)])
    return a, b


def test_shap_is_computed_only_when_asked_and_never_for_the_departures_board(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    shap_calls: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlshap")
    store.write_poll(pg_engine, PollResult(arrivals=[_arrival(trip, 1, "100101", 150, now)]))
    store.upsert_latest(pg_engine, [_vehicle(trip, 150, 2, now)])

    board = client.get("/api/stops/200101/departures")
    assert board.status_code == 200 and shap_calls == []

    first = client.get("/api/vehicles/V/9/2").json()
    assert first["next_stops"][0]["why"] is not None and len(shap_calls) == 1
    again = client.get("/api/vehicles/V/9/2").json()
    assert again["next_stops"][0]["why"] == first["next_stops"][0]["why"]
    assert len(shap_calls) == 1  # the second poll is served from the short cache

    client.get("/api/predictions", params={"trip_id": trip})
    assert len(shap_calls) <= 2  # at most one explanation per request


def test_the_explained_stop_prediction_is_the_first_one_returned(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    a, b = _two_live_trips(network, pg_engine, now)

    body = client.get("/api/predictions", params={"stop_id": "200101", "limit": 5}).json()

    items = [p for p in body["predictions"] if p["trip_id"] in (a, b)]
    assert [p["trip_id"] for p in items] == sorted(
        (a, b), key=lambda t: next(p["scheduled_at"] for p in items if p["trip_id"] == t)
    )
    explained = [p for p in body["predictions"] if p["why"] is not None]
    assert len(explained) == 1
    assert body["predictions"][0]["why"] is not None  # on the entry returned first


def _row_for(model_file: str, **changes):
    from app.features import FeatureRow
    from app.ml.model import DelayModel

    model = DelayModel.load(model_file)
    inputs = {name: 0.0 for name in model.feature_names}
    inputs.update(origin_delay_s=100.0, vehicle_delay_s=120.0, stops_ahead=2.0, sched_run_s=120.0)
    fields = dict(
        trip_id="t",
        line="9",
        stop_id="s",
        stop_seq=3,
        scheduled_at=datetime.now(UTC),
        stops_remaining=0,
        current_delay_s=120,
        delay_trend_s_per_min=None,
        previous_stop_delay_s=None,
        hour_local=12,
        weekday=1,
        route_mean_delay_s=None,
        active_alerts=0,
        model_inputs=inputs,
    )
    return FeatureRow(**{**fields, **changes})


def test_a_late_vehicle_report_does_not_flip_the_answer_between_polls(
    model_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ml.model import DelayModel, ModelPredictor

    predictor = ModelPredictor(DelayModel.load(model_file))
    fresh = _row_for(model_file)
    late = _row_for(model_file, model_inputs=None, model_pause="vehicle_report")

    (first,) = predictor.predict([fresh], explain=True)
    (second,) = predictor.predict([late], explain=True)  # the next poll: report arrived late

    assert first.model_status == "model" and first.explanation is not None
    assert second.model_status == "held"
    assert (second.predicted_delay_s, second.lower_delay_s, second.upper_delay_s) == (
        first.predicted_delay_s,
        first.lower_delay_s,
        first.upper_delay_s,
    )
    assert second.explanation == first.explanation  # the reason does not disappear either

    # Never beyond the hold: after a minute the baseline answers and says why.
    monkeypatch.setattr(ModelPredictor, "HOLD_SECONDS", 0.0)
    (third,) = predictor.predict([late])
    assert third.model_status == "paused" and third.pause_reason == "vehicle_report"
    assert third.lower_delay_s is None and third.explanation is None
    assert third.predicted_delay_s == 120  # persistence of the vehicle's delay


def test_nothing_is_held_without_a_live_vehicle_or_for_another_stop(model_file: str) -> None:
    from app.ml.model import DelayModel, ModelPredictor

    predictor = ModelPredictor(DelayModel.load(model_file))
    predictor.predict([_row_for(model_file)])
    gone = _row_for(model_file, model_inputs=None, model_pause="no_vehicle", current_delay_s=None)
    other_stop = _row_for(model_file, model_inputs=None, model_pause="vehicle_report", stop_id="x")

    (a,) = predictor.predict([gone])
    (b,) = predictor.predict([other_stop])

    assert a.model_status == "paused" and a.pause_reason == "no_vehicle" and a.basis == "scheduled"
    assert b.model_status == "paused"


def test_a_row_without_inputs_and_without_a_pause_reason_is_labelled_no_inputs(
    model_file: str,
) -> None:
    from app.ml.model import DelayModel, ModelPredictor

    predictor = ModelPredictor(DelayModel.load(model_file))
    bare = _row_for(model_file, model_inputs=None, current_delay_s=30, model_pause=None)
    paused = _row_for(model_file, model_inputs=None, current_delay_s=30, model_pause="vehicle_report")

    (a,) = predictor.predict([bare], explain=True)
    (b,) = predictor.predict([paused], explain=True)

    assert a.basis == "live_delay" and a.model_status == "no_inputs"
    assert a.pause_reason is None and a.explanation is None
    assert a.lower_delay_s is None and a.upper_delay_s is None
    assert b.model_status == "paused" and b.pause_reason == "vehicle_report"


def test_a_vehicle_report_just_over_the_cap_is_paused_with_a_reason_in_the_api(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlpause")
    store.write_poll(pg_engine, PollResult(arrivals=[_arrival(trip, 1, "100101", 150, now)]))
    vehicle = _vehicle(trip, 150, 2, now) | {"observed_at": now - timedelta(seconds=150)}
    store.upsert_latest(pg_engine, [vehicle])

    body = client.get("/api/vehicles/V/9/2").json()

    first = body["next_stops"][0]
    assert first["model_status"] == "paused" and first["pause_reason"] == "vehicle_report"
    assert first["why"] is None and first["lower_delay_seconds"] is None
    assert first["delay_seconds"] == 150  # the baseline still answers


def test_reasons_come_back_in_the_requested_language(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
    model_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", model_file)
    monkeypatch.setenv("PREDICTOR", "model")
    now = datetime.now(UTC).replace(microsecond=0)
    trip = _today_trip(network, now, -1, "mlpl")
    store.write_poll(pg_engine, PollResult(arrivals=[_arrival(trip, 1, "100101", 150, now)]))
    store.upsert_latest(pg_engine, [_vehicle(trip, 150, 2, now)])

    english = client.get("/api/vehicles/V/9/2").json()["next_stops"][0]["why"]["summary"]
    polish = client.get("/api/vehicles/V/9/2", params={"lang": "pl"}).json()["next_stops"][0]
    by_trip = client.get("/api/predictions", params={"trip_id": trip, "lang": "pl"}).json()

    assert english.startswith("Now ")
    assert polish["why"]["summary"].startswith("Teraz pojazd ")
    assert by_trip["predictions"][0]["why"]["summary"].startswith("Teraz pojazd ")
    assert client.get("/api/vehicles/V/9/2", params={"lang": "de"}).status_code == 422
