"""Fit the three quantile heads (median, lower, upper) on the delay *change* since the latest known
arrival, then widen the interval by split-conformal calibration on a later, untouched fold."""

from __future__ import annotations

import math
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np

from app.ml.featurize import FEATURE_NAMES

from .dataset import Samples

NOMINAL = 0.8
CALENDAR_FEATURES = ("hour_frac", "weekday")
# Learned from one evening only, these would memorise the clock rather than generalise.
SMALL_DATA_EXCLUDED = (*CALENDAR_FEATURES, "stop_seq")

PARAMS = {
    "num_leaves": 15,
    "learning_rate": 0.1,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "verbose": -1,
    "num_threads": 2,
    "seed": 1,
    "deterministic": True,
}
MAX_ROUNDS = 800
PATIENCE = 30


@dataclass
class Heads:
    features: list[str]
    boosters: dict[str, lgb.Booster]  # "lower", "median", "upper"
    shift_s: float  # conformal widening of the interval, seconds
    rounds: dict[str, int]

    def columns(self) -> list[int]:
        return [FEATURE_NAMES.index(name) for name in self.features]

    def predict(self, samples: Samples) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Median, lower and upper bound of the delay at the target stop, seconds."""
        x = samples.X[:, self.columns()]
        base = samples.base_delay
        median = base + self.boosters["median"].predict(x)
        lower = np.minimum(base + self.boosters["lower"].predict(x), median) - self.shift_s
        upper = np.maximum(base + self.boosters["upper"].predict(x), median) + self.shift_s
        return median, lower, upper


def fit(
    train: Samples,
    early_stop: Samples,
    calibration: Samples,
    exclude: tuple[str, ...] = SMALL_DATA_EXCLUDED,
) -> Heads:
    features = [name for name in FEATURE_NAMES if name not in exclude]
    cols = [FEATURE_NAMES.index(name) for name in features]
    alphas = {"lower": (1 - NOMINAL) / 2, "median": 0.5, "upper": 1 - (1 - NOMINAL) / 2}
    boosters, rounds = {}, {}
    for head, alpha in alphas.items():
        data = lgb.Dataset(train.X[:, cols], train.residual, feature_name=features)
        valid = lgb.Dataset(early_stop.X[:, cols], early_stop.residual, reference=data)
        booster = lgb.train(
            {**PARAMS, "objective": "quantile", "alpha": alpha},
            data,
            MAX_ROUNDS,
            valid_sets=[valid],
            callbacks=[lgb.early_stopping(PATIENCE, verbose=False)],
        )
        boosters[head] = booster
        rounds[head] = booster.best_iteration
    heads = Heads(features, boosters, 0.0, rounds)
    heads.shift_s = conformal_shift(heads, calibration)
    return heads


def conformal_shift(heads: Heads, calibration: Samples) -> float:
    """Seconds to add on both sides so that about ``NOMINAL`` of calibration arrivals fall inside
    (conformalized quantile regression). Never negative: intervals are only ever widened."""
    _, lower, upper = heads.predict(calibration)
    y = calibration.y_delay
    scores = np.maximum(lower - y, y - upper)
    n = len(scores)
    rank = min(math.ceil((n + 1) * NOMINAL), n)
    return float(max(np.sort(scores)[rank - 1], 0.0))
