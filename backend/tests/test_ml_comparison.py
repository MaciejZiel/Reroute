"""The ridge comparison model, the interval calibration and their path through ``evaluate``.

These need numpy only (not LightGBM), so they run wherever numpy is installed."""

import csv
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="numpy is needed to build training samples")

from app.ml.model import DelayModel  # noqa: E402
from ml import calibration, credibility, dataset, evaluate, linear, report, svg  # noqa: E402
from ml.synthetic import BASE, synthetic_events  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "ml_tiny_delay_model.json"


@pytest.fixture(scope="module")
def parts() -> dict[str, dataset.Samples]:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    fold = dataset.time_folds(events).assign(samples)
    return {name: samples.take(fold == i) for i, name in enumerate(dataset.FOLDS)}


FEATURES = [name for name in dataset.FEATURE_NAMES if name not in ("hour_frac", "weekday")]


def test_ridge_recovers_a_linear_relation_and_ignores_the_penalty_it_does_not_need(parts) -> None:
    train = parts["train"]
    column = dataset.FEATURE_NAMES.index("origin_delay_s")
    # A target that is exactly linear in one feature: the fit must find it and predict it.
    exact = dataset.Samples(
        **{**train.__dict__, "y_delay": train.base_delay + 0.25 * train.X[:, column] + 7}
    )
    model = linear.fit(exact, exact, FEATURES, alphas=(0.0001, 1.0))
    predicted = model.predict(exact)
    assert np.abs(predicted - exact.y_delay).max() < 0.05
    assert linear.coefficients(model)[0][0] == "origin_delay_s"


def test_ridge_handles_unknown_features_and_a_constant_column(parts) -> None:
    train = parts["train"]
    X = train.X.copy()
    X[::3, dataset.FEATURE_NAMES.index("leader_delay_s")] = np.nan
    X[:, dataset.FEATURE_NAMES.index("is_tram")] = 1.0  # no spread at all
    damaged = dataset.Samples(**{**train.__dict__, "X": X})
    model = linear.fit(damaged, parts["early_stop"], FEATURES)
    out = model.predict(parts["test"])
    assert out.shape == (len(parts["test"]),) and np.isfinite(out).all()
    assert FEATURES.index("leader_delay_s") in model.missing
    assert model.alpha in linear.ALPHAS


def test_ridge_penalty_is_chosen_on_the_early_stopping_fold_not_on_the_test_fold(parts) -> None:
    train, early = parts["train"], parts["early_stop"]
    first = linear.fit(train, early, FEATURES)
    # Scrambling the test fold cannot change anything: it is never looked at.
    again = linear.fit(train, early, FEATURES)
    assert first.alpha == again.alpha and np.allclose(first.coef, again.coef)


def test_coverage_is_the_share_inside_and_names_the_side_of_every_miss(parts) -> None:
    test = parts["test"]
    y = test.y_delay
    lower, upper = y - 5, y + 5
    lower[::4] = y[::4] + 1  # entirely above the arrival: the arrival is "below" the interval
    upper[1::4] = y[1::4] - 1  # entirely below it: the arrival is "above"
    report_ = calibration.interval_report(test, lower, upper, 0.8, evaluate.horizon_masks(test))
    assert report_["coverage"].value == pytest.approx(0.5, abs=0.01)
    assert report_["below_share"] == pytest.approx(0.25, abs=0.01)
    assert report_["above_share"] == pytest.approx(0.25, abs=0.01)
    assert report_["n"] == len(test)
    assert report_["coverage"].low <= report_["coverage"].value <= report_["coverage"].high
    assert report_["mean_width_s"] > 0
    assert sum(row["n"] for row in report_["by_horizon"].values()) == len(test)
    assert sum(row["n"] for row in report_["by_hour"].values()) == len(test)


def test_hours_are_local_warsaw_time_of_the_moment_of_prediction() -> None:
    # 2026-10-13 20:30 UTC is 22:30 in Warsaw (CEST); 2026-01-13 20:30 UTC is 21:30 (CET).
    summer = 1791923400.0
    winter = 1768336200.0
    assert calibration.local_hours(np.array([summer, winter])).tolist() == [22, 21]


def test_a_bin_with_few_predictions_is_marked_and_ignored_by_the_worst_gap(parts) -> None:
    test = parts["test"]
    hours = calibration.local_hours(test.t0)
    y = test.y_delay
    # Make the interval useless in exactly one hour, which we then thin out to a handful.
    lower, upper = y - 5, y + 5
    first = int(hours[0])
    one_hour = hours == first
    lower[one_hour] = y[one_hour] + 1
    thin = np.flatnonzero(one_hour)[:5]
    keep = ~one_hour
    keep[thin] = True
    kept = test.take(keep)
    report_ = calibration.interval_report(
        kept, lower[keep], upper[keep], 0.8, evaluate.horizon_masks(kept)
    )
    row = report_["by_hour"][f"{first:02d}:00"]
    assert row["n"] == 5 and not row["enough_data"]
    assert calibration.worst_gap(report_) < 0.25  # the 5 useless predictions do not count


def test_evaluate_scores_the_ridge_model_through_the_same_path(parts) -> None:
    def persistence_with_noise(samples: dataset.Samples):
        median = samples.base_delay
        return median, median - 40, median + 40

    ridge = linear.fit(parts["train"], parts["early_stop"], FEATURES)
    result = evaluate.evaluate_predictions(
        persistence_with_noise, 0.0, parts["train"], parts["calibration"], parts["test"], 0.8, ridge
    )
    assert evaluate.RIDGE in result["scores"] and evaluate.GBT in result["scores"]
    same_keys = set(result["scores"][evaluate.GBT]) == set(result["scores"][evaluate.RIDGE])
    assert same_keys
    assert result["gain_vs_ridge"].low <= result["gain_vs_ridge"].high
    assert result["calibration"]["nominal"] == 0.8
    assert result["calibration_window"]["n"] == len(parts["calibration"])
    # The ridge model is reported but never chosen as the baseline the serving rule uses.
    assert result["best_baseline"] != evaluate.RIDGE
    without = evaluate.evaluate_predictions(
        persistence_with_noise, 0.0, parts["train"], parts["calibration"], parts["test"], 0.8
    )
    assert without["gain_vs_ridge"] is None and evaluate.RIDGE not in without["scores"]
    assert without["promoted"] == result["promoted"]


def test_the_results_tables_show_the_ridge_row_and_both_calibration_splits(parts) -> None:
    ridge = linear.fit(parts["train"], parts["early_stop"], FEATURES)
    result = evaluate.evaluate_predictions(
        lambda s: (s.base_delay, s.base_delay - 30, s.base_delay + 30),
        0.0,
        parts["train"],
        parts["calibration"],
        parts["test"],
        0.8,
        ridge,
    )
    card = report._plain(result)
    table = report.results_table(card)
    assert "| ridge regression (same features) |" in table
    assert "scheduled time to the stop" in table and "hour of the day (Warsaw time)" in table
    assert "Trees against the ridge model" in table
    assert "Calibration window" in table
    assert json.dumps(card)  # the card stays plain JSON


def test_coverage_chart_marks_held_out_and_tuned_windows() -> None:
    rows = [
        ("20:00", 0.81, 0.79, 0.83, 4000, "early-stop window", False),
        ("21:00", 0.799, 0.79, 0.81, 150000, "held out", True),
    ]
    text = svg.coverage_dots("Coverage", rows, 0.8, "note")
    assert text.startswith("<svg") and text.count("<circle") == 2
    assert 'fill="var(--model)" stroke' in text and "nominal 80%" in text
    assert "n = 150,000" in text and "early-stop window" in text


def test_credibility_adds_to_the_card_without_touching_the_trees(tmp_path: Path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["card"]["data"]["last_event_epoch"] = BASE + 10_000_000
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(document), encoding="utf-8")
    events_path = _write_events(tmp_path)
    out = tmp_path / "docs"
    card, problems = credibility.add_credibility(model_path, events_path, None, out)
    assert problems == []  # the tiny card states no counts to disagree with

    stored = json.loads(model_path.read_text(encoding="utf-8"))
    assert stored["heads"] == document["heads"] and stored["version"] == document["version"]
    assert stored["card"]["promoted"] is True and stored["card"]["note"] == document["card"]["note"]
    assert evaluate.RIDGE in stored["card"]["scores"]
    assert set(stored["card"]["calibration"]["by_hour"]) and "linear" in stored["card"]
    assert stored["card"]["linear"]["coefficients"][0]["feature"] in dataset.FEATURE_NAMES
    assert DelayModel.load(model_path).version == document["version"]
    for name in (
        "mae_by_horizon.svg",
        "calibration_by_horizon.svg",
        "calibration_by_hour.svg",
        "results.md",
        "report.json",
    ):
        assert (out / name).read_text(encoding="utf-8").strip()
    assert card["calibration"]["n"] > 0


def test_credibility_refuses_data_that_is_not_what_the_model_was_trained_on(tmp_path: Path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["card"]["data"] = {"last_event_epoch": BASE + 10_000_000, "arrivals": 5}
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(document), encoding="utf-8")
    events_path = _write_events(tmp_path)
    with pytest.raises(SystemExit) as stop:
        credibility.add_credibility(model_path, events_path, None, tmp_path / "docs")
    assert "does not match" in str(stop.value) and "the card says 5" in str(stop.value)
    assert json.loads(model_path.read_text(encoding="utf-8")) == document  # nothing was written


def test_a_rebuild_never_changes_the_serving_decision(tmp_path: Path) -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["card"] |= {
        "promoted": True,
        "best_baseline": "x",
        "gain_vs_best_baseline": {"value": 1},
    }
    document["card"]["data"]["last_event_epoch"] = BASE + 10_000_000
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(document), encoding="utf-8")
    credibility.add_credibility(model_path, _write_events(tmp_path), None, tmp_path / "docs")
    card = json.loads(model_path.read_text(encoding="utf-8"))["card"]
    assert card["promoted"] is True and card["best_baseline"] == "x"
    assert card["gain_vs_best_baseline"] == {"value": 1}


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _write_events(tmp_path: Path) -> Path:
    path = tmp_path / "events.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(dataset.EVENT_COLUMNS)
        for e in synthetic_events():
            writer.writerow(
                [
                    "2025-10-09",
                    e.trip_id,
                    e.stop_seq,
                    e.stop_id,
                    e.line,
                    _iso(e.scheduled_s),
                    _iso(e.actual_s),
                    e.delay_s,
                    round(e.known_s - e.actual_s),
                ]
            )
    return path
