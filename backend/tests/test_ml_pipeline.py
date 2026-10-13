"""Time split without leakage, baselines, metrics and the end-to-end export."""

import json
import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("lightgbm")

from app.ml.featurize import Event  # noqa: E402
from app.ml.model import DelayModel, ModelPredictor  # noqa: E402
from ml import baselines, dataset, evaluate, report, train  # noqa: E402
from ml.metrics import ClusterBootstrap  # noqa: E402

BASE = 1_760_000_000.0


def synthetic_events(trips: int = 120, stops: int = 14, seed: int = 5) -> list[Event]:
    """Delays that persist with a drift and a pull towards a line level, one trip every minute."""
    rng = np.random.default_rng(seed)
    events = []
    for number in range(trips):
        delay = float(rng.normal(60, 90))
        line = str(10 + number % 4)
        for seq in range(1, stops + 1):
            delay += rng.normal(0, 12) - 0.05 * delay
            scheduled = BASE + number * 60 + seq * 90
            actual = scheduled + delay
            events.append(
                Event(
                    f"T{number}", seq, f"s{seq}", line, scheduled, actual, round(delay), actual + 30
                )
            )
    return events


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
    boot = ClusterBootstrap(samples.trip, resamples=200)
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
    assert obs.latest("T", 125.0) == 20  # the newest observation at or before the moment
    assert math.isnan(obs.latest("T", 98.0))  # nothing observed yet
    assert math.isnan(obs.latest("T", 400.0))  # too old to be "now"
    assert math.isnan(obs.latest("other", 125.0))


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
