"""Error metrics with cluster bootstrap confidence intervals."""

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


def _percentiles(boot: np.ndarray) -> tuple[float, float]:
    """2.5th and 97.5th percentile, ignoring resamples without a value (NaN); NaN if none has."""
    if np.isnan(boot).all():
        return float("nan"), float("nan")
    low, high = np.nanpercentile(boot, [2.5, 97.5])
    return float(low), float(high)


class ClusterBootstrap:
    """Resamples whole clusters of samples instead of single predictions.

    Predictions that share a trip, a line or a stretch of the evening are strongly dependent (the
    same recent delays feed their features and the same conditions move their errors), so
    resampling single rows would give intervals far too narrow. Callers pass one cluster key per
    sample (``Samples.clusters()``: the line and the 15-minute block of the prediction moment).
    Clusters are still treated as independent of each other, so a shock that hits the whole
    network at once, or a different day, is not covered."""

    def __init__(self, clusters: np.ndarray, seed: int = 7, resamples: int = RESAMPLES) -> None:
        self.groups, self.inverse = np.unique(clusters, return_inverse=True)
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
        point = float(total.sum() / count.sum()) if count.sum() else float("nan")
        drawn = self.draws @ count
        # A resample that holds no sample of the bin has no mean: leave it out rather than count
        # it as zero (which would drag a share such as coverage towards 0).
        with np.errstate(invalid="ignore", divide="ignore"):
            boot = np.where(drawn > 0, (self.draws @ total) / np.where(drawn > 0, drawn, 1), np.nan)
        low, high = _percentiles(boot)
        return Interval(point, low, high)

    def rmse(self, errors: np.ndarray, mask: np.ndarray | None = None) -> Interval:
        squares = self.mean(errors**2, mask)
        return Interval(squares.value**0.5, max(squares.low, 0) ** 0.5, squares.high**0.5)

    def relative_gain(self, baseline_abs: np.ndarray, model_abs: np.ndarray) -> Interval:
        """How much smaller the model's mean absolute error is than the baseline's, as a
        fraction of the baseline's, on the same resampled trips."""
        base, model = self._per_group(baseline_abs), self._per_group(model_abs)
        point = 1 - model.sum() / base.sum()
        boot = 1 - (self.draws @ model) / (self.draws @ base)
        low, high = _percentiles(boot)
        return Interval(float(point), low, high)
