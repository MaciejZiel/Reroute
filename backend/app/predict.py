"""Delay prediction seam: a ``Predictor`` turns feature rows into predicted delays.

The shipped ``BaselinePredictor`` assumes the current delay of the trip persists. A trained model
is a class with the same two methods; select it with ``PREDICTOR=package.module:ClassName``.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .features import FeatureRow


@dataclass(frozen=True)
class Prediction:
    predicted_delay_s: int
    lower_delay_s: int | None = None
    upper_delay_s: int | None = None
    # "live_delay": derived from the delay of the vehicle running the trip.
    # "scheduled": no live vehicle, so the timetable is the best estimate.
    basis: str = "scheduled"


class Predictor(Protocol):
    name: str
    version: str
    kind: str  # "baseline" or "model"

    def predict(self, rows: Sequence[FeatureRow]) -> list[Prediction]: ...


class BaselinePredictor:
    """The delay a vehicle has now is the delay it will have at the stop."""

    name = "persistence"
    version = "1"
    kind = "baseline"

    def predict(self, rows: Sequence[FeatureRow]) -> list[Prediction]:
        return [
            Prediction(row.current_delay_s, basis="live_delay")
            if row.current_delay_s is not None
            else Prediction(0, basis="scheduled")
            for row in rows
        ]


_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    """The configured predictor, created once."""
    global _predictor
    if _predictor is None:
        spec = os.getenv("PREDICTOR", "")
        if spec:
            module_name, _, class_name = spec.partition(":")
            _predictor = getattr(importlib.import_module(module_name), class_name)()
        else:
            _predictor = BaselinePredictor()
    return _predictor
