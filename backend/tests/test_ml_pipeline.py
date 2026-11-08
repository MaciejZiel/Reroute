"""Time split without leakage, baselines, metrics and the end-to-end export."""

import json
import math

import pytest

np = pytest.importorskip(
    "numpy", reason="training libraries are installed out of tree (see README)"
)
pytest.importorskip("lightgbm", reason="training libraries are installed out of tree (see README)")

from app.ml.model import DelayModel, ModelPredictor  # noqa: E402
from ml import baselines, dataset, evaluate, report, train  # noqa: E402
from ml.metrics import ClusterBootstrap  # noqa: E402
from ml.synthetic import BASE, synthetic_events  # noqa: E402


def test_folds_are_ordered_in_time_and_drop_samples_that_straddle_a_boundary() -> None:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    folds = dataset.time_folds(events)
    fold = folds.assign(samples)
    assert (fold < 0).any()  # the embargo exists
    for number in range(4):
        part = samples.take(fold == number)
        assert len(part) > 0
        # everything a prediction in this fold knows and predicts lies inside its window
        assert part.t0.min() >= folds.cuts[number]
        assert part.target_s.max() <= folds.cuts[number + 1]
    # a prediction is never made after the arrival it predicts
    assert (samples.t0 < samples.target_s).all()


def test_baselines_and_clustered_bootstrap() -> None:
    samples = dataset.build_samples(synthetic_events())
    assert np.all(baselines.scheduled(samples) == 0)
    assert np.allclose(baselines.persistence(samples), np.round(samples.origin_delay))
    train_part = samples.take(np.arange(len(samples)) < len(samples) // 2)
    median = baselines.HistoricalMedian(train_part).predict(samples)
    assert median.shape == (len(samples),) and not np.isnan(median).any()
    boot = ClusterBootstrap(samples.clusters(), resamples=200)
    interval = boot.mean(np.abs(samples.residual))
    assert interval.low <= interval.value <= interval.high
    gain = boot.relative_gain(np.ones(len(samples)), np.full(len(samples), 0.9))
    assert gain.value == pytest.approx(0.1) and gain.low == pytest.approx(0.1)


def test_end_to_end_training_exports_a_model_the_server_side_code_reproduces(tmp_path) -> None:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    folds = dataset.time_folds(events)
    fold = folds.assign(samples)
    parts = {name: samples.take(fold == i) for i, name in enumerate(dataset.FOLDS)}
    heads = train.fit(parts["train"], parts["early_stop"], parts["calibration"], exclude=())
    result = evaluate.evaluate(heads, parts["train"], parts["calibration"], parts["test"], 0.8)
    assert 0.5 < result["interval"]["model_coverage"].value <= 1.0
    assert result["best_baseline"] in result["scores"]

    card = report._plain(
        {**result, "data": {"last_event_epoch": BASE}, "interval": result["interval"]}
    )
    document = report.model_document(
        heads, card, {"max_stops_ahead": 12, "max_sched_run_s": 1e9, "max_origin_age_s": 600}
    )
    path = tmp_path / "model.json"
    report.write_json(path, document, compact=True)

    model = DelayModel.load(path)
    assert model.version == document["version"] and model.version.startswith("2025")
    median, lower, upper = heads.predict(parts["test"])
    names = model.feature_names
    columns = [dataset.FEATURE_NAMES.index(n) for n in names]
    for i in range(0, len(parts["test"]), max(len(parts["test"]) // 40, 1)):
        inputs = dict(zip(dataset.FEATURE_NAMES, parts["test"].X[i], strict=True))
        got = model.predict_one(inputs)
        assert abs(got.predicted_delay_s - median[i]) <= 1  # rounded to whole seconds
        assert abs(got.lower_delay_s - lower[i]) <= 1 and abs(got.upper_delay_s - upper[i]) <= 1
        assert got.lower_delay_s <= got.predicted_delay_s <= got.upper_delay_s
        assert got.explanation is not None
        x = parts["test"].X[i, columns].tolist()
        phi, bias = model.heads["median"].contributions(x)
        assert math.isclose(sum(phi) + bias, model.heads["median"].predict(x), abs_tol=1e-6)
    assert json.loads(path.read_text())["card"]["promoted"] in (True, False)
    assert ModelPredictor(model).kind == "model"


def test_vehicle_delay_is_looked_up_at_the_prediction_moment_and_never_from_later() -> None:
    obs = dataset.Observations({"T": ([100.0, 120.0, 140.0], [10, 20, 30])})
    assert obs.latest("T", 125.0) == (
        20,
        5.0,
    )  # the newest observation at or before the moment, and its age
    assert math.isnan(obs.latest("T", 98.0)[0])  # nothing observed yet
    assert math.isnan(obs.latest("T", 400.0)[0])  # too old to be "now"
    assert math.isnan(obs.latest("other", 125.0)[0])


def test_the_base_of_the_prediction_is_the_vehicle_delay_when_known() -> None:
    events = synthetic_events(30)
    observations = dataset.Observations(
        {
            trip: ([e.known_s for e in events if e.trip_id == trip], [999] * 14)
            for trip in {e.trip_id for e in events}
        }
    )
    with_live = dataset.build_samples(events, observations=observations)
    without = dataset.build_samples(events)
    assert np.all(with_live.base_delay == 999)
    assert np.allclose(without.base_delay, without.origin_delay)


def test_sparser_moments_give_fewer_samples_with_the_same_columns() -> None:
    events = synthetic_events(30)
    observations = dataset.Observations(
        {
            trip: ([e.known_s for e in events if e.trip_id == trip], [60] * 14)
            for trip in {e.trip_id for e in events}
        }
    )
    every_minute = dataset.build_samples(events, observations=observations)
    again = dataset.build_samples(events, observations=observations, moment_every_s=60.0)
    sparse = dataset.build_samples(events, observations=observations, moment_every_s=240.0)
    assert np.array_equal(every_minute.X, again.X, equal_nan=True)
    assert 0 < len(sparse) < len(every_minute)
    for samples in (every_minute, sparse):
        assert samples.X.shape == (len(samples), len(dataset.FEATURE_NAMES))
        assert samples.X.dtype == samples.y_delay.dtype == samples.t0.dtype == np.float64
        assert len(samples.trip) == len(samples.lag) == len(samples.trip_id) == len(samples)


def test_frozen_evaluation_scores_only_predictions_after_the_cutoff_and_fits_nothing(
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    from ml import frozen

    events = synthetic_events()
    samples = dataset.build_samples(events)
    folds = dataset.time_folds(events)
    fold = folds.assign(samples)
    parts = [samples.take(fold == i) for i in range(4)]
    heads = train.fit(parts[0], parts[1], parts[2], exclude=())
    card = {"data": {"last_event_epoch": BASE}, "interval": {"nominal": 0.8}, "promoted": True}
    model_path = tmp_path / "model.json"
    report.write_json(
        model_path,
        report.model_document(
            heads, card, {"max_stops_ahead": 12, "max_sched_run_s": 1e9, "max_origin_age_s": 600}
        ),
        compact=True,
    )
    csv_path = tmp_path / "events.csv"

    def stamp(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, UTC).isoformat()

    csv_path.write_text(
        "service_date,trip_id,stop_seq,stop_id,line,scheduled_at,actual_at,delay_s,gap_s\n"
        + "".join(
            f"2025-10-09,{e.trip_id},{e.stop_seq},{e.stop_id},{e.line},{stamp(e.scheduled_s)},"
            f"{stamp(e.actual_s)},{e.delay_s},{round(e.known_s - e.actual_s)}\n"
            for e in events
        )
    )
    cutoff = folds.cuts[3]

    result = frozen.evaluate_frozen(model_path, csv_path, None, since=cutoff)

    assert result["samples"] > 0
    assert result["first_sample"] >= cutoff
    assert result["share_answered_by_model"] == 1.0
    assert result["model_version"] == json.loads(model_path.read_text())["version"]
    text = frozen.render(result)
    assert "Frozen model" in text and "persistence" in text
    nothing = frozen.evaluate_frozen(model_path, csv_path, None, since=BASE * 2)
    assert nothing["samples"] == 0
    assert sum(row["n"] for row in result["by_day"].values()) == result["samples"]
    capped = frozen.evaluate_frozen(model_path, csv_path, None, since=cutoff, per_day=5)
    assert all(row["n"] <= 5 for row in capped["by_day"].values())
    assert capped["samples"] == sum(row["n"] for row in capped["by_day"].values())
    assert "Warsaw day" in frozen.render(capped)

    from ml import decision

    decided = decision.evaluate_decisions(model_path, csv_path, None, since=cutoff)
    assert decided["tuned_on"] > 0 and decided["scored_on"] > 0
    assert decided["split"] >= cutoff
    for row in decided["results"].values():
        for name in ("model", "persistence", "timetable"):
            assert 0.0 <= row[name]["missed"].value <= 1.0 and row[name]["wait_s"] >= 0
    assert "persistence" in decision.render(decided)
    assert decision.evaluate_decisions(model_path, csv_path, None, since=BASE * 2)["samples"] == 0


def test_samples_for_a_subset_of_trips_match_the_full_build_and_concatenate_back() -> None:
    from app.ml.featurize import EventIndex

    events = synthetic_events(30)
    full = dataset.build_samples(events)
    index = EventIndex(events)
    trips = sorted(index.by_trip)
    halves = [trips[: len(trips) // 2], trips[len(trips) // 2 :]]
    parts = [dataset.build_samples(events, index=index, trips=half) for half in halves]
    for half, part in zip(halves, parts, strict=True):
        mask = np.isin(np.array(full.trip_id), half)
        # Features still see every trip's events, so a subset gives exactly the full build's rows.
        np.testing.assert_array_equal(np.sort(full.X[mask], axis=0), np.sort(part.X, axis=0))
    joined = dataset.concat_samples(parts)
    assert len(joined) == len(full)
    assert len(np.unique(joined.trip)) == len(set(joined.trip_id))


def test_observations_merge_interleaved_trips_in_time_order(tmp_path) -> None:
    from ml.dataset import Observations

    path = tmp_path / "observations.csv"
    path.write_text(
        "trip_id,observed_at,delay_s\n"
        "a,2026-11-01T10:02:00+00:00,20\n"
        "b,2026-11-01T10:00:00+00:00,5\n"
        "a,2026-11-01T10:00:00+00:00,10\n"
        "b,2026-11-01T10:01:00+00:00,6\n"
        "a,2026-11-01T10:01:00+00:00,15\n"
    )
    loaded = Observations.load(path)
    times, delays = loaded.rows["a"]
    assert list(delays) == [10, 15, 20] and list(times) == sorted(times)
    assert list(loaded.rows["b"][1]) == [5, 6]


def test_relative_mean_cut_compares_means_over_each_strategys_own_rows() -> None:
    from ml.metrics import ClusterBootstrap

    boot = ClusterBootstrap(np.array(["a", "b", "c", "d"] * 5))
    base = np.full(20, 100.0)
    model = np.full(20, 80.0)
    model_mask = np.ones(20, dtype=bool)
    model_mask[:4] = False  # rows the model "missed" would have counted as zero wait
    cut = boot.relative_mean_cut(base, np.ones(20, dtype=bool), model, model_mask)
    assert abs(cut.value - 0.2) < 1e-9 and abs(cut.low - 0.2) < 1e-9
