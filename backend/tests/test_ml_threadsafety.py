"""The predictor's answer caches are shared by every request thread."""

import sys
import threading
from pathlib import Path

import pytest
from test_ml_live import _row_for

from app.ml.model import DelayModel, ModelPredictor

MODEL = str(Path(__file__).parent / "fixtures" / "ml_tiny_delay_model.json")


def test_caches_survive_many_threads_with_constant_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Small caches that are full most of the time: every call evicts, i.e. iterates a dict that
    # other threads are growing (the "dictionary changed size during iteration" failure).
    monkeypatch.setattr(ModelPredictor, "HOLD_SIZE", 300)
    monkeypatch.setattr(ModelPredictor, "CACHE_SIZE", 300)
    monkeypatch.setattr(ModelPredictor, "HOLD_SECONDS", 1000.0)
    monkeypatch.setattr(ModelPredictor, "CACHE_SECONDS", 1000.0)
    predictor = ModelPredictor(DelayModel.load(MODEL))
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def work(worker: int) -> None:
        try:
            start.wait()
            for i in range(100):
                row = _row_for(MODEL, trip_id=f"t{worker}-{i}", stop_id=f"s{i % 7}")
                row.model_inputs["vehicle_delay_s"] = float(60 + (worker * 31 + i) % 300)
                predictor.predict([row], explain=(i % 2 == 0))
                held = _row_for(
                    MODEL,
                    model_inputs=None,
                    model_pause="vehicle_report",
                    trip_id=f"t{worker}-{i}",
                    stop_id=f"s{i % 7}",
                )
                predictor.predict([held])
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert not errors, errors[:3]


class _RacingDict(dict):
    """While one thread iterates it, another thread tries to add a key: what a busy server does."""

    def __init__(self, *args: object, intruder, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.intruder = intruder
        self.intruded = False
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self.intruder()
        except BaseException as error:  # noqa: BLE001 - re-raised in the test
            self.error = error

    def items(self):
        for position, item in enumerate(list(super().items())):
            yield item
            if position == 0 and not self.intruded:
                self.intruded = True
                thread = threading.Thread(target=self._run)
                thread.start()
                thread.join(timeout=0.5)  # blocked by the predictor's lock, or done without it
                self.thread = thread
        # a plain dict would raise RuntimeError here if the intruder had added a key
        if len(self) != len(list(super().items())):
            raise RuntimeError("dictionary changed size during iteration")


def _insert(predictor: ModelPredictor, target: dict, key: tuple, value: tuple) -> None:
    """Another request thread storing a result: it takes the predictor's lock like the code does."""
    assert hasattr(predictor, "_lock"), "the predictor has no lock around its caches"
    with predictor._lock:  # noqa: SLF001
        target[key] = value


def test_evicting_the_hold_cache_cannot_race_with_a_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ModelPredictor, "HOLD_SIZE", 2)
    predictor = ModelPredictor(DelayModel.load(MODEL))
    prediction = predictor.predict([_row_for(MODEL)])[0]
    racing = _RacingDict(
        {("a", "s", "en"): (0.0, prediction), ("b", "s", "en"): (0.0, prediction)},
        intruder=lambda: _insert(predictor, racing, ("c", "s", "en"), (0.0, prediction)),
    )
    predictor._last = racing  # noqa: SLF001

    predictor._remember(("d", "s", "en"), prediction)  # noqa: SLF001

    racing.thread.join(timeout=5)
    assert racing.intruded
    assert racing.error is None, racing.error


def test_evicting_the_explanation_cache_cannot_race_with_a_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ModelPredictor, "CACHE_SIZE", 2)
    predictor = ModelPredictor(DelayModel.load(MODEL))
    row = _row_for(MODEL)
    other = dict(row.model_inputs, vehicle_delay_s=999.0)
    predictor._cached_explained(dict(row.model_inputs), "en")  # noqa: SLF001
    predictor._cached_explained(other, "en")  # noqa: SLF001
    racing = _RacingDict(
        predictor._explained,  # noqa: SLF001
        intruder=lambda: _insert(predictor, racing, ("c",), (0.0, None)),
    )
    predictor._explained = racing  # noqa: SLF001

    predictor._cached_explained(dict(row.model_inputs, stops_ahead=5.0), "en")  # noqa: SLF001

    racing.thread.join(timeout=5)
    assert racing.intruded
    assert racing.error is None, racing.error
