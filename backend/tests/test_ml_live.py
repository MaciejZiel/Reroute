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


def test_live_inputs_equal_training_inputs_at_every_prediction_moment(
    db: Session, pg_engine: Engine
) -> None:
    rows = [
        _row(trip, seq, 15 * seq + 40 * n + (seq % 3) * 7, offset + 3 * seq)
        for n, (trip, offset) in enumerate([("a", 0), ("b", 9), ("c", 21)])
        for seq in range(1, 8)
    ]
    store.write_poll(pg_engine, PollResult(arrivals=rows))
    events = _events(rows)
    samples = dataset.build_samples(events, lags=(1, 2, 4))
    assert len(samples) > 20

    checked = 0
    for i in range(0, len(samples), 3):
        trip, stop = samples.trip_id[i], samples.stop_id[i]
        target = next(e for e in events if e.trip_id == trip and e.stop_id == stop)
        now = datetime.fromtimestamp(samples.t0[i], UTC)
        index = load_index(db, [trip], ["9"], [stop], now)
        (live,) = model_inputs(
            index,
            [TargetStop(trip, target.stop_seq, stop, "9", target.scheduled_s)],
            now,
        )
        assert live is not None
        for name, expected in zip(FEATURE_NAMES, samples.X[i], strict=True):
            got = live[name]
            assert got == pytest.approx(expected, abs=1e-6) or (
                math.isnan(got) and math.isnan(expected)
            ), (name, got, expected)
        checked += 1
    assert checked > 5


def test_a_trip_without_recorded_stops_has_no_model_inputs(db: Session) -> None:
    now = T0
    index = load_index(db, ["2026-10-13:9:X:none"], ["9"], ["s3"], now)
    target = TargetStop("2026-10-13:9:X:none", 3, "s3", "9", now.timestamp())
    assert model_inputs(index, [target], now) == [None]


@pytest.fixture(scope="module")
def model_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    fold = dataset.time_folds(events).assign(samples)
    parts = [samples.take(fold == i) for i in range(4)]
    heads = train.fit(parts[0], parts[1], parts[2], exclude=())
    card = {"data": {"last_event_epoch": BASE}, "interval": {"nominal": 0.8}, "promoted": True}
    path = tmp_path_factory.mktemp("model") / "delay_model.json"
    report.write_json(path, report.model_document(heads, card), compact=True)
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
    assert first["why"]["summary"].startswith("Expected")
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
