"""``GET /api/model``: static card of the served model, no database involved."""

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import predict
from app.model_card import router

FIXTURE = Path(__file__).parent / "fixtures" / "ml_tiny_delay_model.json"


def _interval(value: float, low: float, high: float) -> dict:
    return {"value": value, "low": low, "high": high}


def _score(mae: float) -> dict:
    return {
        "mae": _interval(mae, mae - 1, mae + 1),
        "rmse": _interval(mae * 1.4, mae * 1.3, mae * 1.5),
        "bias": {"value": -1.5, "low": -2, "high": -1},
        "by_horizon": {"under 2 min": {"n": 10, "mae": _interval(mae / 2, 0, 1), "rmse": {}}},
    }


def _bin(n: int, coverage: float, enough: bool = True) -> dict:
    return {
        "n": n,
        "trips": 5,
        "coverage": _interval(coverage, coverage - 0.01, coverage + 0.01),
        "below_share": 0.1,
        "above_share": 0.1,
        "mean_width_s": 100.0,
        "enough_data": enough,
    }


FULL_CARD = {
    "data": {
        "arrivals": 1000,
        "trips": 50,
        "lines": 4,
        "stops": 14,
        "first_arrival": "2026-10-13T14:25:09Z",
        "last_arrival": "2026-10-13T20:08:56Z",
        "span_hours": 5.73,
        "service_dates": ["2026-10-13"],
        "last_event_epoch": 1791922136.0,
    },
    "folds": {
        "train": {"samples": 900, "from": "2026-10-13T14:26:46Z", "to": "2026-10-13T18:19:27Z"},
        "early_stop": {"samples": 80, "from": "a", "to": "b"},
        "calibration": {"samples": 70, "from": "c", "to": "2026-10-13T19:14:11Z"},
        "test": {"samples": 160, "from": "2026-10-13T19:14:11Z", "to": "2026-10-13T20:08:56Z"},
    },
    "trained_at": "2026-10-13T20:11:25Z",
    "promoted": True,
    "best_baseline": "persistence (BaselinePredictor)",
    "gain_vs_best_baseline": _interval(0.087, 0.073, 0.101),
    "gain_vs_ridge": _interval(0.117, 0.106, 0.129),
    "interval": {"nominal": 0.8, "model_mean_width_s": 132.0},
    "scores": {
        "scheduled (zero delay)": _score(104),
        "persistence (BaselinePredictor)": _score(44.5),
        "ridge regression (same features)": _score(46.0),
        "gradient boosted trees (median)": _score(40.6),
    },
    "calibration": {
        "nominal": 0.8,
        "by_horizon": {"under 2 min": _bin(11859, 0.788), "10 min or more": _bin(10, 0.5, False)},
        "by_hour": {"21:00": _bin(151443, 0.799), "22:00": _bin(13282, 0.798)},
    },
    "calibration_window": {"by_hour": {"20:00": _bin(3000, 0.81), "21:00": _bin(60000, 0.8)}},
}


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, card: dict | None) -> TestClient:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if card is not None:
        document["card"] = copy.deepcopy(card)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("DELAY_MODEL_FILE", str(path))
    monkeypatch.setenv("PREDICTOR", "model")
    monkeypatch.setattr(predict, "_predictor", None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_card_of_the_served_model_is_returned_as_recorded(monkeypatch, tmp_path) -> None:
    body = _client(monkeypatch, tmp_path, FULL_CARD).get("/api/model").json()
    assert body["status"] == "model" and body["promoted"] is True
    assert body["version"] == json.loads(FIXTURE.read_text())["version"]
    assert body["trained_at"] == "2026-10-13T20:11:25Z"
    assert body["training_window"] == {"from": "2026-10-13T14:26:46Z", "to": "2026-10-13T19:14:11Z"}
    assert body["test_window"]["to"] == "2026-10-13T20:08:56Z"
    assert (
        body["data"]["service_dates"] == ["2026-10-13"] and "last_event_epoch" not in body["data"]
    )
    assert body["sample_counts"]["test"] == 160
    assert body["gain_vs_best_baseline"]["value"] == pytest.approx(0.087)
    assert body["gain_vs_ridge"]["low"] == pytest.approx(0.106)
    assert body["interval_nominal"] == 0.8 and body["interval_mean_width_s"] == 132.0
    assert body["serving_range"]["max_stops_ahead"] == 12
    assert body["features"][0] == "stops_ahead"
    assert any("weekday" in limit for limit in body["limits"])


def test_scores_list_the_comparison_models_but_not_the_degenerate_baselines(
    monkeypatch, tmp_path
) -> None:
    body = _client(monkeypatch, tmp_path, FULL_CARD).get("/api/model").json()
    names = [score["name"] for score in body["scores"]]
    assert names == [
        "persistence (BaselinePredictor)",
        "ridge regression (same features)",
        "gradient boosted trees (median)",
    ]
    trees = body["scores"][-1]
    assert trees["mae_s"]["value"] == 40.6 and trees["mae_by_horizon_s"]["under 2 min"] == 20.3


def test_calibration_is_listed_per_horizon_and_per_hour_without_thin_bins(
    monkeypatch, tmp_path
) -> None:
    body = _client(monkeypatch, tmp_path, FULL_CARD).get("/api/model").json()
    assert [r["label"] for r in body["calibration_by_horizon"]] == ["under 2 min"]
    hours = [(r["label"], r["held_out"]) for r in body["calibration_by_hour"]]
    assert hours == [("20:00", False), ("21:00", True), ("21:00", False), ("22:00", True)]
    held = [r for r in body["calibration_by_hour"] if r["held_out"]]
    assert held[0]["predictions"] == 151443 and held[0]["coverage"]["value"] == 0.799
    assert all(r["window"] == "held out" for r in held)


def test_a_card_without_the_new_checks_still_answers(monkeypatch, tmp_path) -> None:
    response = _client(monkeypatch, tmp_path, None).get("/api/model")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "model" and body["scores"] == []
    assert body["calibration_by_horizon"] == [] and body["calibration_by_hour"] == []
    assert body["gain_vs_ridge"] is None and body["training_window"] is None


def test_without_a_model_the_baseline_is_reported(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DELAY_MODEL_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("PREDICTOR", raising=False)
    monkeypatch.setattr(predict, "_predictor", None)
    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/api/model").json()
    assert body["status"] == "baseline" and body["version"] is None and body["scores"] == []
    assert body["limits"]


def test_the_committed_model_file_has_a_card_this_endpoint_can_read(monkeypatch) -> None:
    from app.ml.model import DelayModel
    from app.model_card import card_response

    committed = Path(__file__).resolve().parents[1] / "models" / "delay_model.json"
    response = card_response(DelayModel.load(committed))
    assert response.version and response.promoted is True
    names = [score.name for score in response.scores]
    assert "gradient boosted trees (median)" in names
    assert "ridge regression (same features)" in names
    assert response.calibration_by_horizon and response.calibration_by_hour
    nominal = response.interval_nominal
    held = [r for r in response.calibration_by_hour if r.held_out]
    assert held and all(abs(r.coverage.value - nominal) < 0.05 for r in held)
