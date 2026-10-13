"""Model file, explainability plots and the machine-readable report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.ml.featurize import FEATURE_NAMES

from . import svg
from .dataset import Samples
from .export import ensemble_from_booster
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


def write_plots(
    out: Path, heads: Heads, evaluation: dict, sample: Samples
) -> dict[str, list[tuple[str, float]]]:
    out.mkdir(parents=True, exist_ok=True)
    best = evaluation["best_baseline"]
    model = "gradient boosted trees (median)"
    horizons = list(evaluation["scores"][model]["by_horizon"])
    series = {
        f"{best}": [evaluation["scores"][best]["by_horizon"][h]["mae"].value for h in horizons],
        "gradient boosted trees": [
            evaluation["scores"][model]["by_horizon"][h]["mae"].value for h in horizons
        ],
    }
    (out / "mae_by_horizon.svg").write_text(
        svg.grouped_bars("Held-out MAE by scheduled time to the stop", horizons, series)
    )
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
