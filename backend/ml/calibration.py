"""How often the 80% interval really contains the arrival, and where it does not.

Coverage is the share of arrivals whose delay lies inside [lower, upper]. A calibrated 80%
interval covers about 80% everywhere; an interval that is right on average can still be too
narrow for far stops or for one hour of the evening, which is what the splits here show.
Every share comes with a bootstrap interval that resamples clusters of one line and one
15-minute block (see ``Samples.clusters``).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from app.timeutil import TIMEZONE

from .dataset import Samples
from .metrics import ClusterBootstrap

MIN_BIN = 200  # a bin with fewer predictions than this is listed but not trusted
MIN_TRIPS = 30  # ... and so is one with fewer trips: its predictions are not independent draws


def local_hours(moments: np.ndarray) -> np.ndarray:
    """Hour of the Warsaw clock (0 to 23) of each epoch second."""
    hours = {}
    for minute in np.unique((moments // 60).astype(np.int64)):
        hours[minute] = datetime.fromtimestamp(float(minute * 60), TIMEZONE).hour
    return np.array([hours[m] for m in (moments // 60).astype(np.int64)], dtype=int)


def _bin(
    boot: ClusterBootstrap,
    samples: Samples,
    lower: np.ndarray,
    upper: np.ndarray,
    mask: np.ndarray,
) -> dict:
    y = samples.y_delay
    inside = ((y >= lower) & (y <= upper)).astype(float)
    n = int(mask.sum())
    trips = int(len(np.unique(samples.trip[mask])))
    return {
        "n": n,
        "trips": trips,
        "coverage": boot.mean(inside, mask),
        "below_share": boot.mean((y < lower).astype(float), mask).value,
        "above_share": boot.mean((y > upper).astype(float), mask).value,
        "mean_width_s": float((upper - lower)[mask].mean()),
        "enough_data": n >= MIN_BIN and trips >= MIN_TRIPS,
    }


def interval_report(
    samples: Samples,
    lower: np.ndarray,
    upper: np.ndarray,
    nominal: float,
    horizons: list[tuple[str, np.ndarray]],
) -> dict:
    """Coverage overall, per scheduled time to the stop (``horizons``: label and mask) and per
    local hour of the moment the prediction was made. ``below_share`` is how often the arrival was
    earlier than the interval, ``above_share`` later (a calibrated interval misses about equally
    on both sides)."""
    if len(samples) == 0:
        return {"nominal": nominal, "n": 0, "by_horizon": {}, "by_hour": {}}
    boot = ClusterBootstrap(samples.clusters())
    everything = np.ones(len(samples), dtype=bool)
    by_horizon = {}
    for label, mask in horizons:
        if mask.any():
            by_horizon[label] = _bin(boot, samples, lower, upper, mask)
    hours = local_hours(samples.t0)
    by_hour = {}
    for hour in np.unique(hours):
        by_hour[f"{int(hour):02d}:00"] = _bin(boot, samples, lower, upper, hours == hour)
    overall = _bin(boot, samples, lower, upper, everything)
    return {"nominal": nominal, **overall, "by_horizon": by_horizon, "by_hour": by_hour}


def worst_gap(report: dict) -> float:
    """Largest distance between a trusted bin's coverage and the nominal level."""
    gaps = [
        abs(row["coverage"].value - report["nominal"])
        for part in ("by_horizon", "by_hour")
        for row in report[part].values()
        if row["enough_data"]
    ]
    return max(gaps, default=0.0)
