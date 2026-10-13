"""Model file, explainability plots and the machine-readable report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.ml.featurize import FEATURE_NAMES

from . import svg
from .dataset import Samples
from .evaluate import GBT, RIDGE
from .export import ensemble_from_booster

if TYPE_CHECKING:  # lightgbm is only needed to train, not to write the report of a stored model
    from .train import Heads

LABELS = {
    "stops_ahead": "stops ahead of the vehicle",
    "seconds_since_origin": "time since the last recorded stop (s)",
    "origin_delay_s": "delay at the last stop (s)",
    "vehicle_delay_s": "delay reported by the vehicle now (s)",
    "vehicle_minus_origin_s": "delay change since the last stop (s)",
    "prev_delay_change_s": "delay change at the last stop (s)",
    "trend_s_per_min": "delay trend (s per min)",
    "sched_run_s": "scheduled running time left (s)",
    "stop_seq": "stop number in the trip",
    "hour_frac": "hour of day",
    "weekday": "weekday",
    "is_tram": "is a tram",
    "line_recent_delay_s": "line delay, last 15 min (s)",
    "stop_recent_delay_s": "stop delay, last 30 min (s)",
    "leader_delay_s": "delay of the vehicle ahead (s)",
    "headway_s": "scheduled gap to the vehicle ahead (s)",
}


def _plain(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, np.generic):
        return value.item()
    return value


def importance(heads: Heads, sample: Samples) -> list[tuple[str, float]]:
    """Mean absolute SHAP value (seconds) of the median head over a sample of arrivals."""
    contrib = heads.boosters["median"].predict(sample.X[:, heads.columns()], pred_contrib=True)
    mean_abs = np.abs(contrib[:, :-1]).mean(axis=0)
    return sorted(zip(heads.features, mean_abs.tolist(), strict=True), key=lambda i: -i[1])


def partial_dependence(heads: Heads, sample: Samples, feature: str, points: int = 24):
    """Average predicted delay change when ``feature`` is set to each grid value."""
    cols = heads.columns()
    x = sample.X[:, cols].copy()
    column = heads.features.index(feature)
    values = x[:, column]
    grid = np.unique(np.nanquantile(values, np.linspace(0.02, 0.98, points)))
    effect = []
    for value in grid:
        x[:, column] = value
        effect.append(float(heads.boosters["median"].predict(x).mean()))
    return grid.tolist(), effect


def write_comparison_plots(out: Path, evaluation: dict) -> None:
    """MAE by horizon for persistence, the ridge model and the trees, and the interval coverage."""
    out.mkdir(parents=True, exist_ok=True)
    scores = evaluation["scores"]
    horizons = list(scores[GBT]["by_horizon"])
    best = evaluation["best_baseline"]
    series = {best: [scores[best]["by_horizon"][h]["mae"].value for h in horizons]}
    if RIDGE in scores:
        series["ridge regression"] = [scores[RIDGE]["by_horizon"][h]["mae"].value for h in horizons]
    series["gradient boosted trees"] = [scores[GBT]["by_horizon"][h]["mae"].value for h in horizons]
    (out / "mae_by_horizon.svg").write_text(
        svg.grouped_bars("Held-out MAE by scheduled time to the stop", horizons, series)
    )
    calibration = evaluation["calibration"]
    nominal = calibration["nominal"]
    windows = [("held out", True, calibration)]
    for name, held_out in (("calibration window", False), ("early-stop window", False)):
        key = name.split()[0].replace("-", "_") + "_window"
        if key in evaluation and evaluation[key]["by_hour"]:
            windows.append((name, held_out, evaluation[key]))
    for part, name, title, note in (
        (
            "by_horizon",
            "calibration_by_horizon.svg",
            "80% interval coverage by scheduled time to the stop",
            "held-out arrivals inside the interval, with the 95% trip-bootstrap interval",
        ),
        (
            "by_hour",
            "calibration_by_hour.svg",
            "80% interval coverage by hour of the day (Warsaw time)",
            "filled: held out; hollow: windows used to tune the model, so less independent",
        ),
    ):
        rows = []
        for window, held_out, block in windows if part == "by_hour" else windows[:1]:
            for label, r in sorted(block[part].items()):
                if r["enough_data"]:
                    c = r["coverage"]
                    rows.append((label, c.value, c.low, c.high, r["n"], window, held_out))
        if part == "by_hour":
            rows.sort(key=lambda row: (row[0], not row[6]))
        (out / name).write_text(svg.coverage_dots(title, rows, nominal, note))


def write_plots(
    out: Path, heads: Heads, evaluation: dict, sample: Samples
) -> dict[str, list[tuple[str, float]]]:
    write_comparison_plots(out, evaluation)
    ranked = importance(heads, sample)
    (out / "importance.svg").write_text(
        svg.horizontal_bars(
            "What the model relies on",
            [(LABELS.get(n, n), v) for n, v in ranked[:10]],
            "mean absolute SHAP value, seconds of delay change (test fold)",
        )
    )
    panels = []
    for name, _ in ranked[:4]:
        grid, effect = partial_dependence(heads, sample, name)
        panels.append((LABELS.get(name, name), grid, effect))
    (out / "partial_dependence.svg").write_text(
        svg.line_panels(
            "Average predicted delay change as one feature varies",
            panels,
            "y: predicted change of delay since the last stop (s); other features as observed",
        )
    )
    return {"importance": ranked}


def linear_summary(ridge) -> dict:
    """What the card says about the comparison model: its penalty and standardised weights."""
    from . import linear

    return {
        "kind": "ridge regression on the target of the trees (delay change since the latest "
        "known delay), same features, missing values replaced by the training mean plus a flag",
        "alpha": ridge.alpha,
        "coefficients": [
            {"feature": name, "seconds_per_sd": round(value, 3)}
            for name, value in linear.coefficients(ridge)
        ],
    }


def _pct(interval: dict) -> str:
    return f"{interval['value']:.1%} ({interval['low']:.1%} to {interval['high']:.1%})"


def results_table(card: dict) -> str:
    """The comparison as markdown (also what the README quotes)."""
    scores, interval = card["scores"], card["interval"]
    horizons = list(next(iter(scores.values()))["by_horizon"])
    lines = [
        "| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | "
        + " | ".join(f"MAE {h}" for h in horizons)
        + " |",
        "| --- | --- | --- | --- | " + " | ".join("---" for _ in horizons) + " |",
    ]
    for name, score in scores.items():
        cells = [f"{score['by_horizon'][h]['mae']['value']:.1f}" for h in horizons]
        mae, rmse = score["mae"], score["rmse"]
        lines.append(
            f"| {name} | {mae['value']:.1f} ({mae['low']:.1f} to {mae['high']:.1f}) "
            f"| {rmse['value']:.1f} ({rmse['low']:.1f} to {rmse['high']:.1f}) "
            f"| {score['bias']['value']:+.1f} | " + " | ".join(cells) + " |"
        )
    cov, naive = interval["model_coverage"], interval["naive_coverage"]
    lines += [
        "",
        f"80% interval on the test fold: model coverage {_pct(cov)}, "
        f"mean width {interval['model_mean_width_s']:.0f} s; "
        f"persistence plus empirical residual quantiles: coverage {_pct(naive)}, "
        f"mean width {interval['naive_mean_width_s']:.0f} s.",
    ]
    ridge_gain = card.get("gain_vs_ridge")
    if ridge_gain:
        lines += [
            "",
            f"Trees against the ridge model: MAE {ridge_gain['value']:.1%} lower "
            f"(bootstrap interval {ridge_gain['low']:.1%} to {ridge_gain['high']:.1%}).",
        ]
    calibration = card.get("calibration")
    if calibration:
        lines += ["", "Held-out test window:", "", *_calibration_tables(calibration)]
        for key, heading in (
            ("calibration_window", "Calibration window (it set the interval width)"),
            ("early_stop_window", "Early-stopping window (it chose the number of trees)"),
        ):
            window = card.get(key)
            if window and window["by_hour"]:
                lines += [
                    "",
                    f"{heading}, by hour only:",
                    "",
                    *_calibration_tables(window, ("by_hour",)),
                ]
    return "\n".join(lines) + "\n"


def _calibration_tables(calibration: dict, parts=("by_horizon", "by_hour")) -> list[str]:
    headings = {
        "by_horizon": "scheduled time to the stop",
        "by_hour": "hour of the day (Warsaw time)",
    }
    lines = []
    for part in parts:
        heading = headings[part]
        lines += [
            f"| {heading} | predictions | trips | coverage (bootstrap 95% interval) "
            "| missed below | missed above | mean width s |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for label, row in calibration[part].items():
            flag = "" if row["enough_data"] else " (few predictions)"
            lines.append(
                f"| {label}{flag} | {row['n']:,} | {row['trips']:,} | {_pct(row['coverage'])} "
                f"| {row['below_share']:.1%} | {row['above_share']:.1%} "
                f"| {row['mean_width_s']:.0f} |"
            )
        lines.append("")
    return lines[:-1]


def model_document(heads: Heads, card: dict, serving_range: dict) -> dict:
    ensembles = {name: ensemble_from_booster(b).to_json() for name, b in heads.boosters.items()}
    digest = hashlib.sha256(json.dumps(ensembles, sort_keys=True).encode()).hexdigest()[:8]
    stamp = datetime.fromtimestamp(card["data"]["last_event_epoch"], UTC).strftime("%Y%m%d")
    return {
        "format": 1,
        "version": f"{stamp}-{digest}",
        "feature_names": heads.features,
        "target": "change of the delay between the latest recorded stop and the target stop, s",
        "interval": {"nominal": card["interval"]["nominal"], "conformal_shift_s": heads.shift_s},
        "card": card,
        "serving_range": serving_range,
        "heads": ensembles,
    }


def write_json(path: Path, document: dict, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(document, separators=(",", ":")) if compact else json.dumps(document, indent=2)
    )
    path.write_text(text + "\n", encoding="utf-8")


__all__ = ["FEATURE_NAMES", "model_document", "write_json", "write_plots", "_plain"]
