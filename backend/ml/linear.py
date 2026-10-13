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
        z = (np.where(unknown, self.mean, x) - self.mean) / self.scale
        flags = unknown[:, self.missing].astype(float)
        return np.hstack([z, flags])

    def predict(self, samples: Samples) -> np.ndarray:
        """Delay at the target stop, seconds: the base delay plus the predicted change."""
        return samples.base_delay + self.design(samples) @ self.coef + self.intercept


def _solve(z: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    """Ridge with an unpenalised intercept, by centring the columns and the target."""
    zc, yc = z - z.mean(axis=0), y - y.mean()
    gram = zc.T @ zc + alpha * np.eye(z.shape[1])
    coef = np.linalg.solve(gram, zc.T @ yc)
    return coef, float(y.mean() - z.mean(axis=0) @ coef)


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
    best: Ridge | None = None
    best_error = np.inf
    for alpha in alphas:
        draft = Ridge(features, mean, scale, missing, np.empty(0), 0.0, alpha)
        coef, intercept = _solve(draft.design(train), train.residual, alpha)
        candidate = Ridge(features, mean, scale, missing, coef, intercept, alpha)
        error = float(np.abs(candidate.predict(early_stop) - early_stop.y_delay).mean())
        if error < best_error:
            best, best_error = candidate, error
    assert best is not None
    return best


def coefficients(model: Ridge) -> list[tuple[str, float]]:
    """Standardised coefficients (seconds of delay change per standard deviation), largest first."""
    named = list(zip(model.features, model.coef[: len(model.features)].tolist(), strict=True))
    return sorted(named, key=lambda item: -abs(item[1]))
