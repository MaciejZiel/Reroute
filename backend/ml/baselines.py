"""Baselines the model has to beat. Every one predicts the delay at the target stop and is fitted
on the training fold only."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import numpy as np

from app.ml.featurize import FEATURE_NAMES
from app.predict import BaselinePredictor
from app.timeutil import TIMEZONE

from .dataset import Samples

HOUR = FEATURE_NAMES.index("hour_frac")
MIN_COUNT = 5


def scheduled(samples: Samples) -> np.ndarray:
    """The timetable: no delay at all."""
    return np.zeros(len(samples))


def persistence(samples: Samples) -> np.ndarray:
    """The shipped ``BaselinePredictor``: the delay a vehicle has now is the delay it will have."""
    from app.features import FeatureRow

    when = datetime.fromtimestamp(0, TIMEZONE)
    predictor = BaselinePredictor()
    rows = [
        FeatureRow(
            trip_id="",
            line="",
            stop_id="",
            stop_seq=0,
            scheduled_at=when,
            stops_remaining=0,
            current_delay_s=int(delay),
            delay_trend_s_per_min=None,
            previous_stop_delay_s=None,
            hour_local=0,
            weekday=0,
            route_mean_delay_s=None,
            active_alerts=0,
        )
        for delay in samples.origin_delay
    ]
    return np.array([p.predicted_delay_s for p in predictor.predict(rows)], dtype=float)


class HistoricalMedian:
    """Median delay in the training fold for (line, stop, hour), falling back to (line, hour),
    then hour, then everything, whenever a cell holds fewer than ``MIN_COUNT`` arrivals."""

    name = "historical median"

    def __init__(self, train: Samples) -> None:
        cells: dict[tuple, list[float]] = defaultdict(list)
        for key, delay in zip(self._keys(train), train.y_delay, strict=True):
            line, stop, hour = key
            cells[(line, stop, hour)].append(delay)
            cells[(line, None, hour)].append(delay)
            cells[(None, None, hour)].append(delay)
        self.median = {key: float(np.median(v)) for key, v in cells.items() if len(v) >= MIN_COUNT}
        self.overall = float(np.median(train.y_delay))

    @staticmethod
    def _keys(samples: Samples):
        hours = samples.X[:, HOUR].astype(int)
        return zip(samples.line, samples.stop_id, hours.tolist(), strict=True)

    def predict(self, samples: Samples) -> np.ndarray:
        out = []
        for line, stop, hour in self._keys(samples):
            for key in ((line, stop, hour), (line, None, hour), (None, None, hour)):
                if key in self.median:
                    out.append(self.median[key])
                    break
            else:
                out.append(self.overall)
        return np.array(out)


class DampedPersistence:
    """Persistence with the shrinkage a straight-line fit on the training fold gives per number of
    stops ahead: ``delay = a[k] + b[k] * current_delay``. The strong version of the baseline."""

    name = "calibrated persistence"

    def __init__(self, train: Samples) -> None:
        self.coefficients: dict[int, tuple[float, float]] = {}
        for lag in np.unique(train.lag):
            mask = train.lag == lag
            slope, intercept = np.polyfit(train.origin_delay[mask], train.y_delay[mask], 1)
            self.coefficients[int(lag)] = (float(intercept), float(slope))

    def predict(self, samples: Samples) -> np.ndarray:
        a = np.array([self.coefficients[int(lag)][0] for lag in samples.lag])
        b = np.array([self.coefficients[int(lag)][1] for lag in samples.lag])
        return a + b * samples.origin_delay
