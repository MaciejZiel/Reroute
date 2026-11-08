"""Score a trained, unchanged model file on data that came after its training window.

Nothing is fitted here: the model is read exactly as the server reads it (the pure-Python
evaluator), the samples are built the same way as for training, and only predictions made after
the model's last training arrival are scored. This is the check that matters: does the margin over
persistence survive on time the model has never seen, with no retraining and no tuning?
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from app.ml.featurize import FEATURE_NAMES, EventIndex
from app.ml.model import DelayModel

from . import dataset
from .evaluate import MIN_RELATIVE_GAIN, horizon_masks
from .metrics import ClusterBootstrap


def parse_utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


WARSAW = ZoneInfo("Europe/Warsaw")


def warsaw_day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, WARSAW).date().isoformat()


def unseen_samples(
    events: list,
    observations: dataset.Observations | None,
    cutoff: float,
    moment_every_s: float | None = None,
    per_day: int | None = None,
    seed: int = 7,
) -> tuple[dataset.Samples, np.ndarray] | None:
    """Samples whose prediction moment is after ``cutoff``, with the Warsaw trip-start day of each;
    None if there are none. Built one day of trips at a time, capped at ``per_day`` (see
    ``evaluate_frozen``)."""
    index = EventIndex(events)
    trips_by_day: dict[str, list[str]] = defaultdict(list)
    for trip_id, trip_events in index.by_trip.items():
        # A trip over before the cutoff can give no prediction after it: skip its simulation.
        if max(e.actual_s for e in trip_events) < cutoff:
            continue
        trips_by_day[warsaw_day(min(e.actual_s for e in trip_events))].append(trip_id)
    parts, day_labels = [], []
    for number, day in enumerate(sorted(trips_by_day)):
        samples = dataset.build_samples(
            events,
            observations=observations,
            moment_every_s=moment_every_s or dataset.MOMENT_EVERY_S,
            index=index,
            trips=trips_by_day[day],
            seed=seed + number,
        )
        samples = samples.take(samples.t0 >= cutoff)
        if per_day is not None and len(samples) > per_day:
            rng = np.random.default_rng(seed + number)
            keep = np.zeros(len(samples), dtype=bool)
            keep[rng.choice(len(samples), size=per_day, replace=False)] = True
            samples = samples.take(keep)
        if len(samples):
            parts.append(samples)
            day_labels.append(np.full(len(samples), day))
    if not parts:
        return None
    return dataset.concat_samples(parts), np.concatenate(day_labels)


def predict_all(
    model: DelayModel, samples: dataset.Samples
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Median, lower and upper delay of every sample, from the served (pure-Python) model."""
    median = np.empty(len(samples))
    lower = np.empty(len(samples))
    upper = np.empty(len(samples))
    for i in range(len(samples)):
        inputs = dict(zip(FEATURE_NAMES, samples.X[i].tolist(), strict=True))
        prediction = model.predict_one(inputs, with_reasons=False)
        median[i], lower[i], upper[i] = (
            prediction.predicted_delay_s,
            prediction.lower_delay_s,
            prediction.upper_delay_s,
        )
    return median, lower, upper


def evaluate_frozen(
    model_path: Path,
    events_path: Path,
    observations_path: Path | None,
    since: float | None = None,
    moment_every_s: float | None = None,
    per_day: int | None = None,
    seed: int = 7,
) -> dict:
    """Samples are built one day of trips at a time (the Warsaw date a trip starts on), so the
    build's working memory is one day; the kept samples are held for all days. With ``per_day`` at
    most that many predictions per day are scored, drawn at random: the evaluator walks every tree
    in pure Python, so scoring millions takes hours, and a few tens of thousands per day give tight
    intervals. The cap weighs every full day the same in the overall figures, not by its traffic;
    the per-day table shows whether that matters (a model better on every day is better overall
    under any weighting)."""
    model = DelayModel.load(model_path)
    events = dataset.load_events(events_path)
    observations = dataset.Observations.load(observations_path) if observations_path else None
    cutoff = since if since is not None else parse_utc(model.card["data"]["last_arrival"])
    built = unseen_samples(events, observations, cutoff, moment_every_s, per_day, seed)
    if built is None:
        return {"samples": 0, "since": cutoff}
    later, days = built
    median, lower, upper = predict_all(model, later)
    y = later.y_delay
    served = np.array(
        [model.in_range(dict(zip(FEATURE_NAMES, row.tolist(), strict=True))) for row in later.X]
    )
    boot = ClusterBootstrap(later.clusters())
    model_err, base_err = np.abs(median - y), np.abs(later.base_delay - y)
    gain = boot.relative_gain(base_err, model_err)
    by_horizon = {}
    for label, mask in horizon_masks(later):
        if mask.any():
            by_horizon[label] = {
                "n": int(mask.sum()),
                "model_mae": boot.mean(model_err, mask).value,
                "persistence_mae": boot.mean(base_err, mask).value,
            }
    by_day = {}
    for day in np.unique(days):
        mask = days == day
        day_boot = ClusterBootstrap(later.take(mask).clusters())
        by_day[str(day)] = {
            "n": int(mask.sum()),
            "model_mae": float(model_err[mask].mean()),
            "persistence_mae": float(base_err[mask].mean()),
            # A tiny day (the cutoff day, a partial last day) can have no persistence error at all.
            "gain": day_boot.relative_gain(base_err[mask], model_err[mask])
            if base_err[mask].sum() > 0
            else None,
            "coverage": float(((y >= lower) & (y <= upper))[mask].mean()),
            "bias": float((median - y)[mask].mean()),
        }
    return {
        "model_version": model.version,
        "since": cutoff,
        "first_sample": float(later.t0.min()),
        "last_sample": float(later.t0.max()),
        "samples": len(later),
        "trips": len(set(later.trip_id)),
        "share_answered_by_model": float(served.mean()),
        "model_mae": boot.mean(model_err),
        "persistence_mae": boot.mean(base_err),
        "scheduled_mae": boot.mean(np.abs(y)),
        "model_rmse": boot.rmse(median - y),
        "persistence_rmse": boot.rmse(later.base_delay - y),
        "gain": gain,
        "coverage": boot.mean(((y >= lower) & (y <= upper)).astype(float)),
        "by_horizon": by_horizon,
        "by_day": by_day,
        "clears_the_serving_rule": bool(gain.value >= MIN_RELATIVE_GAIN and gain.low > 0),
    }


def render(result: dict) -> str:
    if not result["samples"]:
        return "No predictions after the model's training window in this data.\n"

    def clock(epoch: float) -> str:
        return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    gain = result["gain"]
    lines = [
        f"Frozen model {result['model_version']} on predictions from "
        f"{clock(result['first_sample'])} to {clock(result['last_sample'])}: "
        f"{result['samples']:,} samples, {result['trips']:,} trips.",
        "",
        "| | MAE s | RMSE s |",
        "| --- | --- | --- |",
        f"| scheduled (zero delay) | {result['scheduled_mae'].value:.1f} | |",
        f"| persistence | {result['persistence_mae'].value:.1f} | "
        f"{result['persistence_rmse'].value:.1f} |",
        f"| model | {result['model_mae'].value:.1f} | {result['model_rmse'].value:.1f} |",
        "",
        f"Model MAE is {gain.value:.1%} lower than persistence (bootstrap interval "
        f"{gain.low:.1%} to {gain.high:.1%}); "
        f"80% interval coverage {result['coverage'].value:.1%}; "
        f"the serving rule (at least {MIN_RELATIVE_GAIN:.0%} and interval above zero) "
        f"{'is cleared' if result['clears_the_serving_rule'] else 'is NOT cleared'}.",
        "",
        "| scheduled time to the stop | samples | model MAE | persistence MAE |",
        "| --- | --- | --- | --- |",
    ]
    for label, row in result["by_horizon"].items():
        lines.append(
            f"| {label} | {row['n']:,} | {row['model_mae']:.1f} | {row['persistence_mae']:.1f} |"
        )
    lines += [
        "",
        "| Warsaw day (trips starting on it) | samples | model MAE | persistence MAE "
        "| gain (95% interval) | 80% range held | model bias |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for day, row in result.get("by_day", {}).items():
        gain = row["gain"]
        weekday = datetime.fromisoformat(day).strftime("%a")
        gain_text = f"{gain.value:.1%} ({gain.low:.1%} to {gain.high:.1%})" if gain else "n/a"
        lines.append(
            f"| {day} {weekday} | {row['n']:,} | {row['model_mae']:.1f} | "
            f"{row['persistence_mae']:.1f} | {gain_text} "
            f"| {row['coverage']:.1%} | {row['bias']:+.1f} |"
        )
    return "\n".join(lines) + "\n"
