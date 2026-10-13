"""A trained delay model read from its JSON file, and the predictor that serves it.

File layout (``format`` 1): feature names, three tree ensembles (``lower``, ``median``,
``upper``) that predict the change of the delay since the latest recorded stop, the conformal
widening of the interval, and a ``card`` with how the model was trained and scored.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

from ..features import FeatureRow
from ..predict import BaselinePredictor, Prediction
from .ensemble import Ensemble
from .explain import explain
from .featurize import FEATURE_NAMES, base_delay

FORMAT = 1


class DelayModel:
    def __init__(self, data: dict) -> None:
        if data.get("format") != FORMAT:
            raise ValueError(f"unsupported model format {data.get('format')!r}")
        self.version: str = data["version"]
        self.feature_names: list[str] = data["feature_names"]
        unknown = set(self.feature_names) - set(FEATURE_NAMES)
        if unknown:
            raise ValueError(f"model uses features this code cannot compute: {sorted(unknown)}")
        n = len(self.feature_names)
        self.heads = {name: Ensemble.from_json(head, n) for name, head in data["heads"].items()}
        self.shift_s: float = float(data["interval"]["conformal_shift_s"])
        self.card: dict = data.get("card", {})
        self.promoted: bool = bool(self.card.get("promoted", False))
        # What the model was trained and tested on; outside it the baseline answers.
        self.serving_range: dict = data.get("serving_range", {})

    @classmethod
    def load(cls, path: str | Path) -> DelayModel:
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle))

    def in_range(self, inputs: dict[str, float]) -> bool:
        """Whether the inputs lie where the model was trained and its interval calibrated."""
        limits = {
            "stops_ahead": self.serving_range.get("max_stops_ahead"),
            "sched_run_s": self.serving_range.get("max_sched_run_s"),
            "seconds_since_origin": self.serving_range.get("max_origin_age_s"),
        }
        if not self.serving_range:
            return False  # a file that does not state its range is not served
        return all(
            limit is None or not (inputs.get(name, 0.0) > limit) for name, limit in limits.items()
        )

    def vector(self, inputs: dict[str, float]) -> list[float]:
        return [float(inputs.get(name, math.nan)) for name in self.feature_names]

    def predict_one(self, inputs: dict[str, float], with_reasons: bool = True) -> Prediction:
        x = self.vector(inputs)
        origin = base_delay(inputs)
        change = self.heads["median"].predict(x)
        median = origin + change
        lower = min(origin + self.heads["lower"].predict(x), median) - self.shift_s
        upper = max(origin + self.heads["upper"].predict(x), median) + self.shift_s
        reasons = None
        if with_reasons:
            phi, _ = self.heads["median"].contributions(x)
            reasons = explain(self.feature_names, x, phi, origin, median)
        return Prediction(
            round(median), round(lower), round(upper), basis="live_delay", explanation=reasons
        )


class ModelPredictor:
    """Serves a ``DelayModel``.

    Where it must not answer (no live vehicle delay, an old last stop, beyond the trained range of
    stops) the baseline does, so the model never shows a number it was not tested for."""

    kind = "model"
    # Exact SHAP values cost a few milliseconds on a laptop and much more on a small server, and
    # the interface shows one reason: only the first rows of a request carry an explanation.
    EXPLAIN_FIRST = 1

    def __init__(self, model: DelayModel, name: str = "gbt-delay") -> None:
        self.model = model
        self.name = name
        self.version = model.version
        self._fallback = BaselinePredictor()

    def predict(self, rows: Sequence[FeatureRow]) -> list[Prediction]:
        fallback = self._fallback.predict(rows)
        result = []
        explained = 0
        for row, plain in zip(rows, fallback, strict=True):
            if not row.model_inputs or not self.model.in_range(row.model_inputs):
                result.append(plain)
                continue
            result.append(
                self.model.predict_one(dict(row.model_inputs), explained < self.EXPLAIN_FIRST)
            )
            explained += 1
        return result
