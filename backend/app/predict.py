"""Delay prediction seam: a ``Predictor`` turns feature rows into predicted delays.

The shipped ``BaselinePredictor`` assumes the current delay of the trip persists. A trained model
(``app.ml``) is picked up from a file, see ``get_predictor``.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .features import FeatureRow
from .ml.explain import Explanation


@dataclass(frozen=True)
class Prediction:
    predicted_delay_s: int
    lower_delay_s: int | None = None
    upper_delay_s: int | None = None
    # "live_delay": derived from the delay of the vehicle running the trip.
    # "scheduled": no live vehicle, so the timetable is the best estimate.
    basis: str = "scheduled"
    explanation: Explanation | None = None


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

DEFAULT_MODEL_FILE = Path(__file__).resolve().parents[1] / "models" / "delay_model.json"
log = logging.getLogger(__name__)


def load_model_predictor(path: Path) -> Predictor:
    from .ml.model import DelayModel, ModelPredictor

    return ModelPredictor(DelayModel.load(path))


def _auto_predictor() -> Predictor:
    """The trained model if its file exists and its card says it beat the baselines on held-out
    future data; otherwise the baseline. Retraining replaces the file; nothing else changes."""
    path = Path(os.getenv("DELAY_MODEL_FILE") or DEFAULT_MODEL_FILE)
    if not path.is_file():
        return BaselinePredictor()
    try:
        predictor = load_model_predictor(path)
    except (OSError, ValueError, KeyError) as error:
        log.warning("ignoring unreadable delay model %s: %s", path, error)
        return BaselinePredictor()
    if not predictor.model.promoted:  # type: ignore[attr-defined]
        log.info(
            "delay model %s did not beat the baseline; serving the baseline", predictor.version
        )
        return BaselinePredictor()
    return predictor


def get_predictor() -> Predictor:
    """The configured predictor, created once.

    ``PREDICTOR=model`` serves the model file whatever its card says, ``PREDICTOR=pkg.mod:Class``
    a custom class, and nothing set picks the promoted model if there is one.
    """
    global _predictor
    if _predictor is None:
        spec = os.getenv("PREDICTOR", "")
        if spec == "model":
            path = Path(os.getenv("DELAY_MODEL_FILE") or DEFAULT_MODEL_FILE)
            try:
                _predictor = load_model_predictor(path)
            except (OSError, ValueError, KeyError) as error:
                log.warning(
                    "PREDICTOR=model but %s is unusable (%s); serving the baseline", path, error
                )
                _predictor = BaselinePredictor()
        elif spec:
            module_name, _, class_name = spec.partition(":")
            _predictor = getattr(importlib.import_module(module_name), class_name)()
        else:
            _predictor = _auto_predictor()
    return _predictor
