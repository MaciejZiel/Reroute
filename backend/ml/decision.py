"""Would a passenger who follows the advice catch the vehicle, and how long would they wait?

The question behind a "will I make it?" feature: the app names a moment to be at the stop, the
passenger is there at that moment, and the vehicle either has already gone (missed) or comes later
(the wait). Every strategy names that moment as the scheduled time plus a delay it expects:

- ``model``: the model's lower bound, plus a margin per horizon;
- ``persistence``: the vehicle's current delay, plus a margin per horizon (a quantile of how
  persistence was wrong, which is the cheap interval any app could build);
- ``timetable``: no live data at all, the timetable plus a margin per horizon.

Each margin is fitted on the first part of the data so that the strategy misses the target share
of vehicles (10 %, 5 %) in every horizon, and all strategies are then scored on the later part.
With the same miss rate, the strategy whose passengers wait less gives the better advice. The
model is read unchanged and only data after its training window is used.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from app.ml.model import DelayModel

from . import dataset
from .evaluate import horizon_masks
from .frozen import WARSAW, parse_utc, predict_all, unseen_samples
from .metrics import ClusterBootstrap

TARGET_MISSES = (0.10, 0.05)


def margins(error: np.ndarray, horizons: list[np.ndarray], miss: float) -> list[float]:
    """Per horizon, the shift that leaves ``miss`` of the vehicles arriving before the named
    moment: the ``miss`` quantile of (true delay − expected delay)."""
    return [float(np.quantile(error[mask], miss)) if mask.any() else 0.0 for mask in horizons]


def leave_point(expected: np.ndarray, horizons: list[np.ndarray], shift: list[float]) -> np.ndarray:
    point = expected.astype(float).copy()
    for mask, value in zip(horizons, shift, strict=True):
        point[mask] += value
    return point


def evaluate_decisions(
    model_path: Path,
    events_path: Path,
    observations_path: Path | None,
    since: float | None = None,
    moment_every_s: float | None = None,
    per_day: int | None = None,
    tune_share: float = 0.5,
    seed: int = 7,
) -> dict:
    model = DelayModel.load(model_path)
    events = dataset.load_events(events_path)
    observations = dataset.Observations.load(observations_path) if observations_path else None
    cutoff = since if since is not None else parse_utc(model.card["data"]["last_arrival"])
    built = unseen_samples(events, observations, cutoff, moment_every_s, per_day, seed)
    if built is None:
        return {"samples": 0, "since": cutoff}
    samples, _ = built
    _, lower, _ = predict_all(model, samples)
    y = samples.y_delay
    expected = {
        "model": lower,
        "persistence": samples.base_delay,
        "timetable": np.zeros(len(samples)),
    }
    # Margins come from the earlier part, scores from the later one: no peeking at the scored data.
    split = float(np.quantile(samples.t0, tune_share))
    tune, test = samples.t0 < split, samples.t0 >= split
    labels = [label for label, _ in horizon_masks(samples)]
    horizons = [mask for _, mask in horizon_masks(samples)]
    scored = samples.take(test)
    boot = ClusterBootstrap(scored.clusters(), seed=seed)
    results = {}
    for miss in TARGET_MISSES:
        waits, row = {}, {}
        for name, guess in expected.items():
            shift = margins(y[tune] - guess[tune], [h[tune] for h in horizons], miss)
            point = leave_point(guess, horizons, shift)[test]
            truth = y[test]
            missed = truth < point
            # A missed vehicle is not a wait: it is counted in the miss rate instead.
            wait = np.where(missed, 0.0, truth - point)
            waits[name] = (wait, ~missed)
            caught = ~missed
            row[name] = {
                "missed": boot.mean(missed.astype(float)),
                "wait_s": float(wait[caught].mean()),
                "median_wait_s": float(np.median(wait[caught])),
                "by_horizon": {
                    label: {
                        "missed": float(missed[h[test]].mean()),
                        "wait_s": float(wait[h[test] & caught].mean()),
                    }
                    for label, h in zip(labels, horizons, strict=True)
                    if (h[test] & caught).any()
                },
            }
        # Mean wait of the passengers who caught their vehicle, compared like for like: a missed
        # vehicle is not a short wait.
        row["wait_cut_vs_persistence"] = boot.relative_mean_cut(
            *waits["persistence"], *waits["model"]
        )
        row["wait_cut_vs_timetable"] = boot.relative_mean_cut(*waits["timetable"], *waits["model"])
        results[miss] = row
    return {
        "model_version": model.version,
        "since": cutoff,
        "split": split,
        "first_sample": float(samples.t0.min()),
        "last_sample": float(samples.t0.max()),
        "tuned_on": int(tune.sum()),
        "scored_on": int(test.sum()),
        "trips": len(set(scored.trip_id)),
        "results": results,
    }


def render(result: dict) -> str:
    if not result.get("scored_on"):
        return "No predictions after the model's training window in this data.\n"

    def clock(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, WARSAW).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"Model {result['model_version']}, data after its training window: margins fitted on "
        f"{clock(result['first_sample'])} to {clock(result['split'])} Warsaw time "
        f"({result['tuned_on']:,} predictions), scored on {clock(result['split'])} to "
        f"{clock(result['last_sample'])} ({result['scored_on']:,} predictions, "
        f"{result['trips']:,} trips).",
        "",
        "A passenger is at the stop at the moment the strategy names. Missed: the vehicle had "
        "already gone. Wait: seconds at the stop when caught.",
        "",
    ]
    for miss, row in result["results"].items():
        lines += [
            f"Target: miss {miss:.0%} of vehicles",
            "",
            "| strategy | missed (95% interval) | mean wait s | median wait s |",
            "| --- | --- | --- | --- |",
        ]
        for name in ("model", "persistence", "timetable"):
            r = row[name]
            m = r["missed"]
            lines.append(
                f"| {name} | {m.value:.1%} ({m.low:.1%} to {m.high:.1%}) | "
                f"{r['wait_s']:.0f} | {r['median_wait_s']:.0f} |"
            )
        for key, label in (
            ("wait_cut_vs_persistence", "persistence"),
            ("wait_cut_vs_timetable", "timetable"),
        ):
            g = row[key]
            lines.append(
                f"\nWait cut by the model vs {label}: {g.value:.1%} "
                f"(95% interval {g.low:.1%} to {g.high:.1%})."
            )
        lines += [
            "",
            "| horizon | model missed / wait | persistence missed / wait |",
            "| --- | --- | --- |",
        ]
        for label, r in row["model"]["by_horizon"].items():
            p = row["persistence"]["by_horizon"].get(label)
            if p:
                lines.append(
                    f"| {label} | {r['missed']:.1%} / {r['wait_s']:.0f} s | "
                    f"{p['missed']:.1%} / {p['wait_s']:.0f} s |"
                )
        lines.append("")
    return "\n".join(lines)
