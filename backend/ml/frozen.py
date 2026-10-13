"""Score a trained, unchanged model file on data that came after its training window.

Nothing is fitted here: the model is read exactly as the server reads it (the pure-Python
evaluator), the samples are built the same way as for training, and only predictions made after
the model's last training arrival are scored. This is the check that matters: does the margin over
persistence survive on time the model has never seen, with no retraining and no tuning?
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from app.ml.featurize import FEATURE_NAMES
from app.ml.model import DelayModel

from . import dataset
from .evaluate import MIN_RELATIVE_GAIN, horizon_masks
from .metrics import ClusterBootstrap


def parse_utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def evaluate_frozen(
    model_path: Path,
    events_path: Path,
    observations_path: Path | None,
    since: float | None = None,
) -> dict:
    model = DelayModel.load(model_path)
    events = dataset.load_events(events_path)
    observations = dataset.Observations.load(observations_path) if observations_path else None
    cutoff = since if since is not None else parse_utc(model.card["data"]["last_arrival"])
    samples = dataset.build_samples(events, observations=observations)
    later = samples.take(samples.t0 >= cutoff)
    if len(later) == 0:
        return {"samples": 0, "since": cutoff}
    median = np.empty(len(later))
    lower = np.empty(len(later))
    upper = np.empty(len(later))
    for i in range(len(later)):
        inputs = dict(zip(FEATURE_NAMES, later.X[i].tolist(), strict=True))
        prediction = model.predict_one(inputs, with_reasons=False)
        median[i], lower[i], upper[i] = (
            prediction.predicted_delay_s,
            prediction.lower_delay_s,
            prediction.upper_delay_s,
        )
    y = later.y_delay
    served = np.array(
        [model.in_range(dict(zip(FEATURE_NAMES, row.tolist(), strict=True))) for row in later.X]
    )
    boot = ClusterBootstrap(later.trip)
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
    return {
        "model_version": model.version,
        "since": cutoff,
        "first_sample": float(later.t0.min()),
        "last_sample": float(later.t0.max()),
        "samples": len(later),
        "trips": int(len(np.unique(later.trip))),
        "share_answered_by_model": float(served.mean()),
        "model_mae": boot.mean(model_err),
        "persistence_mae": boot.mean(base_err),
        "scheduled_mae": boot.mean(np.abs(y)),
        "model_rmse": boot.rmse(median - y),
        "persistence_rmse": boot.rmse(later.base_delay - y),
        "gain": gain,
        "coverage": boot.mean(((y >= lower) & (y <= upper)).astype(float)),
        "by_horizon": by_horizon,
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
    return "\n".join(lines) + "\n"
