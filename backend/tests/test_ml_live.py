"""The model's live inputs equal its training inputs, and the API serves model, reasons and
interval; without a promoted model the baseline answers."""

import json
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

pytest.importorskip("lightgbm")

from test_live_api import _today_trip, _vehicle, client, network  # noqa: E402, F401
from test_ml_pipeline import BASE, synthetic_events  # noqa: E402

from app import predict  # noqa: E402
from app.collector import store  # noqa: E402
from app.collector.derive import PollResult  # noqa: E402
from app.ml.featurize import FEATURE_NAMES, Event, TargetStop  # noqa: E402
from app.ml.live import load_index, model_inputs  # noqa: E402
from ml import dataset, report, train  # noqa: E402

DAY = date(2026, 10, 13)
RANGE = {"max_stops_ahead": 12, "max_sched_run_s": 1e9, "max_origin_age_s": 600}
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


def _observations(rows: list[dict]) -> dataset.Observations:
    """A vehicle report every 20 s from the first to the last arrival of each trip."""
    series: dict[str, tuple[list[float], list[int]]] = {}
    for trip in {r["trip_id"] for r in rows}:
        mine = [r for r in rows if r["trip_id"] == trip]
        start = min(r["actual_at"] for r in mine).timestamp()
        end = max(r["actual_at"] for r in mine).timestamp()
        times = [start + 20 * i for i in range(int((end - start) // 20) + 1)]
        series[trip] = (times, [int(30 + (t - start) / 7) for t in times])
    return dataset.Observations(series)


def test_live_inputs_equal_training_inputs_at_arbitrary_moments_with_a_vehicle_delay(
    db: Session, pg_engine: Engine
) -> None:
    rows = [
        _row(trip, seq, 15 * seq + 40 * n + (seq % 3) * 7, offset + 3 * seq)
        for n, (trip, offset) in enumerate([("a", 0), ("b", 9), ("c", 21)])
        for seq in range(1, 8)
    ]
    store.write_poll(pg_engine, PollResult(arrivals=rows))
    events = _events(rows)
    observations = _observations(rows)
    samples = dataset.build_samples(events, lags=(1, 2, 4), observations=observations)
    assert len(samples) > 20
    assert (samples.X[:, FEATURE_NAMES.index("seconds_since_origin")] > 0).any()

    checked = 0
    for i in range(0, len(samples), 3):
        trip, stop = samples.trip_id[i], samples.stop_id[i]
        target = next(e for e in events if e.trip_id == trip and e.stop_id == stop)
        moment = float(samples.t0[i])
        now = datetime.fromtimestamp(moment, UTC)
        times, delays = observations.rows[trip]
        newest = max(k for k, t in enumerate(times) if t <= moment)
        index = load_index(db, [trip], ["9"], [stop], now)
        (live,) = model_inputs(
            index,
            [TargetStop(trip, target.stop_seq, stop, "9", target.scheduled_s)],
            now,
            [delays[newest]],
            [moment - times[newest]],
        )
        assert live is not None
        for name, expected in zip(FEATURE_NAMES, samples.X[i], strict=True):
            got = live[name]
            assert got == pytest.approx(expected, abs=1e-6) or (
                math.isnan(got) and math.isnan(expected)
            ), (name, got, expected)
        checked += 1
    assert checked > 5


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
    assert inputs(last_known + timedelta(seconds=20), 60, 120) is None  # vehicle report too old
    # the 50-minute-old trip of the review: the last recorded stop is far too old
    assert inputs(last_known + timedelta(minutes=50), 60, 5) is None


@pytest.fixture(scope="module")
def model_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    fold = dataset.time_folds(events).assign(samples)
    parts = [samples.take(fold == i) for i in range(4)]
    heads = train.fit(parts[0], parts[1], parts[2], exclude=())
    card = {"data": {"last_event_epoch": BASE}, "interval": {"nominal": 0.8}, "promoted": True}
    path = tmp_path_factory.mktemp("model") / "delay_model.json"
    report.write_json(path, report.model_document(heads, card, RANGE), compact=True)
    yield str(path)


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
