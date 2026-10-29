"""A plain ridge regression on the same features, as the simple model the boosted trees must beat.

It predicts the same target as the trees (the change of the delay since the vehicle's own latest
delay, ``Samples.residual``) from the same columns, and is scored through the same code path
(``evaluate``). It is offline only: nothing here is exported or served. It needs just numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.ml.featurize import FEATURE_NAMES

from .dataset import Samples

ALPHAS = (0.1, 1.0, 10.0, 100.0, 1_000.0, 10_000.0)  # on standardised columns


@dataclass
class Ridge:
    features: list[str]
    mean: np.ndarray  # per column, over the training fold's known values
    scale: np.ndarray
    missing: list[int]  # columns (of ``features``) that had unknown values in training
    coef: np.ndarray
    intercept: float
    alpha: float

    def columns(self) -> list[int]:
        return [FEATURE_NAMES.index(name) for name in self.features]

    def design(self, samples: Samples) -> np.ndarray:
        x = samples.X[:, self.columns()]
        unknown = np.isnan(x)
        z = np.where(unknown, self.mean, x)
        del x
        z -= self.mean  # in place: one copy of the columns instead of three
        z /= self.scale
        flags = unknown[:, self.missing].astype(float)
        return np.hstack([z, flags])

    def predict(self, samples: Samples) -> np.ndarray:
        """Delay at the target stop, seconds: the base delay plus the predicted change."""
        return samples.base_delay + self.design(samples) @ self.coef + self.intercept


class _Normal:
    """The centred normal equations of one design matrix, built once for every penalty: ridge
    with an unpenalised intercept, by centring the columns and the target. ``z`` is centred in
    place, so pass a matrix nothing else uses."""

    def __init__(self, z: np.ndarray, y: np.ndarray) -> None:
        self.z_mean, self.y_mean = z.mean(axis=0), y.mean()
        z -= self.z_mean
        self.gram = z.T @ z
        self.target = z.T @ (y - self.y_mean)

    def solve(self, alpha: float) -> tuple[np.ndarray, float]:
        coef = np.linalg.solve(self.gram + alpha * np.eye(len(self.gram)), self.target)
        return coef, float(self.y_mean - self.z_mean @ coef)


def fit(
    train: Samples,
    early_stop: Samples,
    features: list[str],
    alphas: tuple[float, ...] = ALPHAS,
) -> Ridge:
    """Fit on the training fold; the penalty is chosen by MAE on the early-stopping fold, the same
    fold the trees use for their stopping rule, so the test fold decides nothing."""
    cols = [FEATURE_NAMES.index(name) for name in features]
    x = train.X[:, cols]
    unknown = np.isnan(x)
    count = np.maximum((~unknown).sum(axis=0), 1)
    mean = np.where(unknown, 0.0, x).sum(axis=0) / count
    spread = np.sqrt(np.where(unknown, 0.0, (x - mean) ** 2).sum(axis=0) / count)
    scale = np.where(spread > 0, spread, 1.0)
    missing = [int(i) for i in np.flatnonzero(unknown.any(axis=0))]
    del x, unknown
    # The design does not depend on the penalty: build it once, not once per alpha (on a week of
    # data each copy of the training design is gigabytes).
    draft = Ridge(features, mean, scale, missing, np.empty(0), 0.0, 0.0)
    normal = _Normal(draft.design(train), train.residual)
    held_out = draft.design(early_stop)
    best: Ridge | None = None
    best_error = np.inf
    for alpha in alphas:
        coef, intercept = normal.solve(alpha)
        candidate = Ridge(features, mean, scale, missing, coef, intercept, alpha)
        predicted = early_stop.base_delay + held_out @ coef + intercept
        error = float(np.abs(predicted - early_stop.y_delay).mean())
        if error < best_error:
            best, best_error = candidate, error
    assert best is not None
    return best


def coefficients(model: Ridge) -> list[tuple[str, float]]:
    """Standardised coefficients (seconds of delay change per standard deviation), largest first."""
    named = list(zip(model.features, model.coef[: len(model.features)].tolist(), strict=True))
    return sorted(named, key=lambda item: -abs(item[1]))
