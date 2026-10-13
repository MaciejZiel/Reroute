"""A trained delay model read from its JSON file, and the predictor that serves it.

File layout (``format`` 1): feature names, three tree ensembles (``lower``, ``median``,
``upper``) that predict the change of the delay since the latest recorded stop, the conformal
widening of the interval, and a ``card`` with how the model was trained and scored.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from dataclasses import replace
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

    def predict_one(
        self, inputs: dict[str, float], with_reasons: bool = True, lang: str = "en"
    ) -> Prediction:
        x = self.vector(inputs)
        origin = base_delay(inputs)
        change = self.heads["median"].predict(x)
        median = origin + change
        lower = min(origin + self.heads["lower"].predict(x), median) - self.shift_s
        upper = max(origin + self.heads["upper"].predict(x), median) + self.shift_s
        reasons = None
        if with_reasons:
            phi, _ = self.heads["median"].contributions(x)
            reasons = explain(self.feature_names, x, phi, origin, median, lang)
        return Prediction(
            round(median), round(lower), round(upper), basis="live_delay", explanation=reasons
        )


class ModelPredictor:
    """Serves a ``DelayModel``.

    Where it must not answer (no live vehicle delay, an old last stop, beyond the trained range of
    stops) the baseline does, so the model never shows a number it was not tested for."""

    kind = "model"
    # Exact SHAP values cost tens of milliseconds on a laptop and far more on a small server, and
    # the interface shows one reason: at most one prediction per request is explained, only when
    # asked, and the result is kept for a few seconds for the next poll of the same vehicle.
    CACHE_SECONDS = 20.0
    CACHE_SIZE = 256
    # A vehicle report that is a little late must not make the answer flip between the model and
    # the baseline from one poll to the next: the last model answer for a stop is kept this long.
    HOLD_SECONDS = 60.0
    HOLD_SIZE = 2048

    def __init__(self, model: DelayModel, name: str = "gbt-delay") -> None:
        self.model = model
        self.name = name
        self.version = model.version
        self._fallback = BaselinePredictor()
        self._explained: dict[tuple, tuple[float, Prediction]] = {}
        self._last: dict[tuple, tuple[float, Prediction]] = {}

    def _cached_explained(self, inputs: dict[str, float], lang: str) -> Prediction:
        # The seconds that pass between two polls (age of the report, time since the last stop)
        # do not change the reasons within the TTL, so they are not part of the key.
        key = (
            lang,
            *(
                None if math.isnan(v) else round(v, 1)
                for name, v in inputs.items()
                if name not in ("seconds_since_origin", "vehicle_report_age_s")
            ),
        )
        now = time.monotonic()
        hit = self._explained.get(key)
        if hit and hit[0] > now:
            return hit[1]
        prediction = self.model.predict_one(inputs, True, lang)
        if len(self._explained) >= self.CACHE_SIZE:
            self._explained = {k: v for k, v in self._explained.items() if v[0] > now}
            if len(self._explained) >= self.CACHE_SIZE:
                self._explained.clear()
        self._explained[key] = (now + self.CACHE_SECONDS, prediction)
        return prediction

    def _remember(self, key: tuple, prediction: Prediction) -> None:
        now = time.monotonic()
        if len(self._last) >= self.HOLD_SIZE:
            self._last = {k: v for k, v in self._last.items() if now - v[0] <= self.HOLD_SECONDS}
            if len(self._last) >= self.HOLD_SIZE:
                self._last.clear()
        self._last[key] = (now, prediction)

    def _held(self, key: tuple) -> Prediction | None:
        found = self._last.get(key)
        if found and time.monotonic() - found[0] <= self.HOLD_SECONDS:
            return replace(found[1], model_status="held")
        return None

    def predict(
        self, rows: Sequence[FeatureRow], explain: bool = False, lang: str = "en"
    ) -> list[Prediction]:
        fallback = self._fallback.predict(rows)
        result = []
        explained = False
        for row, plain in zip(rows, fallback, strict=True):
            key = (row.trip_id, row.stop_id, lang)
            if row.model_inputs and self.model.in_range(row.model_inputs):
                inputs = dict(row.model_inputs)
                if explain and not explained:
                    prediction = self._cached_explained(inputs, lang)
                    explained = True
                else:
                    prediction = self.model.predict_one(inputs, False, lang)
                prediction = replace(prediction, model_status="model")
                self._remember(key, prediction)
                result.append(prediction)
            elif row.model_inputs:
                result.append(replace(plain, model_status="out_of_range"))
            elif (held := self._held(key)) is not None and row.current_delay_s is not None:
                # One late vehicle report must not flip the answer: keep the last model answer
                # for a minute, never longer (report cap + this hold stays within the 180 s after
                # which the app itself stops trusting a vehicle's delay).
                result.append(held)
            else:
                status = "paused" if row.model_pause else None
                result.append(replace(plain, model_status=status, pause_reason=row.model_pause))
        return result
