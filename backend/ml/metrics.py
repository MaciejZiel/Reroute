"""Error metrics with trip-clustered bootstrap confidence intervals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RESAMPLES = 1000


@dataclass(frozen=True)
class Interval:
    value: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.value:.1f} [{self.low:.1f}, {self.high:.1f}]"


class ClusterBootstrap:
    """Resamples whole trips: arrivals of one trip are strongly dependent, so resampling single
    predictions would give intervals that are far too narrow."""

    def __init__(self, trips: np.ndarray, seed: int = 7, resamples: int = RESAMPLES) -> None:
        self.groups, self.inverse = np.unique(trips, return_inverse=True)
        rng = np.random.default_rng(seed)
        self.draws = rng.multinomial(
            len(self.groups), np.full(len(self.groups), 1 / len(self.groups)), size=resamples
        ).astype(float)

    def _per_group(self, values: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
        if mask is not None:
            values = np.where(mask, values, 0.0)
        return np.bincount(self.inverse, weights=values, minlength=len(self.groups))

    def mean(self, values: np.ndarray, mask: np.ndarray | None = None) -> Interval:
        """Mean of ``values`` (over ``mask``) with a 95 % interval."""
        total = self._per_group(values, mask)
        count = self._per_group(np.ones(len(values)), mask)
        point = float(total.sum() / count.sum())
        boot = (self.draws @ total) / np.maximum(self.draws @ count, 1)
        low, high = np.percentile(boot, [2.5, 97.5])
        return Interval(point, float(low), float(high))

    def rmse(self, errors: np.ndarray, mask: np.ndarray | None = None) -> Interval:
        squares = self.mean(errors**2, mask)
        return Interval(squares.value**0.5, max(squares.low, 0) ** 0.5, squares.high**0.5)

    def relative_gain(self, baseline_abs: np.ndarray, model_abs: np.ndarray) -> Interval:
        """How much smaller the model's mean absolute error is than the baseline's, as a
        fraction of the baseline's, on the same resampled trips."""
        base, model = self._per_group(baseline_abs), self._per_group(model_abs)
        point = 1 - model.sum() / base.sum()
        boot = 1 - (self.draws @ model) / (self.draws @ base)
        low, high = np.percentile(boot, [2.5, 97.5])
        return Interval(float(point), float(low), float(high))
