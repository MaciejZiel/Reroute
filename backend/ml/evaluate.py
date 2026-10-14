"""Compare the model with the baselines on the held-out future fold."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np

from app.ml.featurize import FEATURE_NAMES

from . import baselines, calibration
from .dataset import Samples
from .metrics import ClusterBootstrap

if TYPE_CHECKING:  # the training library is only needed to fit, not to score
    from .linear import Ridge
    from .train import Heads

Quantiles = Callable[[Samples], tuple[np.ndarray, np.ndarray, np.ndarray]]
RIDGE = "ridge regression (same features)"
GBT = "gradient boosted trees (median)"

HORIZONS = (
    (0, 120, "under 2 min"),
    (120, 300, "2 to 5 min"),
    (300, 600, "5 to 10 min"),
    (600, 1e9, "10 min or more"),
)
MIN_RELATIVE_GAIN = 0.05  # fixed before looking at held-out results
RUN = FEATURE_NAMES.index("sched_run_s")


def horizon_masks(samples: Samples) -> list[tuple[str, np.ndarray]]:
    run = samples.X[:, RUN]
    return [(label, (run >= lo) & (run < hi)) for lo, hi, label in HORIZONS]


def _score(boot: ClusterBootstrap, samples: Samples, predicted: np.ndarray) -> dict:
    error = predicted - samples.y_delay
    result: dict[str, Any] = {
        "mae": boot.mean(np.abs(error)),
        "rmse": boot.rmse(error),
        "bias": boot.mean(error),
        "by_horizon": {},
    }
    for label, mask in horizon_masks(samples):
        if mask.any():
            result["by_horizon"][label] = {
                "n": int(mask.sum()),
                "mae": boot.mean(np.abs(error), mask),
                "rmse": boot.rmse(error, mask),
            }
    return result


class NaiveInterval:
    """Baseline interval: persistence plus the training fold's residual quantiles per lag."""

    def __init__(self, train: Samples, nominal: float) -> None:
        tail = (1 - nominal) / 2
        self.q = {
            int(lag): np.quantile(train.residual[train.lag == lag], [tail, 1 - tail])
            for lag in np.unique(train.lag)
        }

    def predict(self, samples: Samples) -> tuple[np.ndarray, np.ndarray]:
        low = np.array([self.q[int(k)][0] for k in samples.lag])
        high = np.array([self.q[int(k)][1] for k in samples.lag])
        return samples.base_delay + low, samples.base_delay + high


def evaluate(
    heads: Heads,
    train: Samples,
    calibration_fold: Samples,
    test: Samples,
    nominal: float,
    linear: Ridge | None = None,
) -> dict:
    return evaluate_predictions(
        heads.predict, heads.shift_s, train, calibration_fold, test, nominal, linear
    )


def evaluate_predictions(
    predict: Quantiles,
    shift_s: float,
    train: Samples,
    calibration_fold: Samples,
    test: Samples,
    nominal: float,
    linear: Ridge | None = None,
) -> dict:
    """Score a model given as a function returning (median, lower, upper) per sample. Trained
    heads and a model file read back by the server's evaluator both go through here."""
    boot = ClusterBootstrap(test.clusters())
    historical = baselines.HistoricalMedian(train)
    damped = baselines.DampedPersistence(train)
    candidates: dict[str, Callable[[Samples], np.ndarray]] = {
        "scheduled (zero delay)": baselines.scheduled,
        "persistence (BaselinePredictor)": baselines.persistence,
        "historical median (line, stop, hour)": historical.predict,
        "calibrated persistence": damped.predict,
    }
    # The best baseline is chosen on the calibration fold, never on the test fold.
    val_mae = {
        name: float(np.abs(fn(calibration_fold) - calibration_fold.y_delay).mean())
        for name, fn in candidates.items()
    }
    best = min(val_mae, key=val_mae.__getitem__)
    median, lower, upper = predict(test)
    scores = {name: _score(boot, test, fn(test)) for name, fn in candidates.items()}
    scores[GBT] = _score(boot, test, median)
    ridge_gain = None
    if linear is not None:
        # A model, not a baseline: reported next to the others but never the one the serving rule
        # compares with, so adding it cannot change whether the trees are promoted.
        ridge = linear.predict(test)
        scores[RIDGE] = _score(boot, test, ridge)
        ridge_gain = boot.relative_gain(np.abs(ridge - test.y_delay), np.abs(median - test.y_delay))
    best_abs = np.abs(candidates[best](test) - test.y_delay)
    gain = boot.relative_gain(best_abs, np.abs(median - test.y_delay))
    inside = ((test.y_delay >= lower) & (test.y_delay <= upper)).astype(float)
    n_low, n_high = NaiveInterval(train, nominal).predict(test)
    naive_inside = ((test.y_delay >= n_low) & (test.y_delay <= n_high)).astype(float)
    promoted = gain.value >= MIN_RELATIVE_GAIN and gain.low > 0
    return {
        "scores": scores,
        "validation_mae": val_mae,
        "best_baseline": best,
        "gain_vs_best_baseline": gain,
        "interval": {
            "nominal": nominal,
            "model_coverage": boot.mean(inside),
            "model_mean_width_s": float((upper - lower).mean()),
            "naive_coverage": boot.mean(naive_inside),
            "naive_mean_width_s": float((n_high - n_low).mean()),
            "conformal_shift_s": shift_s,
        },
        "calibration": calibration.interval_report(
            test, lower, upper, nominal, horizon_masks(test)
        ),
        "calibration_window": calibration.interval_report(
            calibration_fold,
            *predict(calibration_fold)[1:],
            nominal,
            horizon_masks(calibration_fold),
        ),
        "gain_vs_ridge": ridge_gain,
        "promoted": bool(promoted),
        "rule": (
            f"serve the model by default only if its MAE is at least {MIN_RELATIVE_GAIN:.0%} "
            "below the best baseline's on the test fold and the 95% cluster-bootstrap interval of "
            "that gain stays above zero"
        ),
    }
