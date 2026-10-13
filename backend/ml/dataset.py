"""Training samples from recorded arrivals, and the time-ordered folds they are split into."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from app.ml.featurize import (
    FEATURE_NAMES,
    Event,
    EventIndex,
    TargetStop,
    feature_vector,
    known_trip_events,
)

DEFAULT_LAGS = (1, 2, 3, 4, 6, 8, 12)
EVENT_COLUMNS = (
    "service_date,trip_id,stop_seq,stop_id,line,scheduled_at,actual_at,delay_s,gap_s".split(",")
)


def load_events(path: Path) -> list[Event]:
    """Read the CSV written by ``ml/pull.sh`` (a ``COPY`` of ``stop_arrival_events``)."""
    events = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(EVENT_COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} lacks columns {sorted(missing)}")
        for row in reader:
            actual = datetime.fromisoformat(row["actual_at"]).timestamp()
            events.append(
                Event(
                    trip_id=row["trip_id"],
                    stop_seq=int(row["stop_seq"]),
                    stop_id=row["stop_id"],
                    line=row["line"],
                    scheduled_s=datetime.fromisoformat(row["scheduled_at"]).timestamp(),
                    actual_s=actual,
                    delay_s=int(row["delay_s"]),
                    known_s=actual + int(row["gap_s"]),
                )
            )
    return events


@dataclass
class Samples:
    """One row per (origin arrival, later target stop of the same trip)."""

    X: np.ndarray  # (n, len(FEATURE_NAMES)), NaN where a feature is unknown
    y_delay: np.ndarray  # delay at the target stop, seconds
    t0: np.ndarray  # epoch seconds: when the prediction would have been made
    target_s: np.ndarray  # epoch seconds: when the vehicle actually reached the target
    trip: np.ndarray  # trip index, for cluster bootstrap
    trip_id: list[str]
    line: list[str]
    stop_id: list[str]
    lag: np.ndarray  # stops between origin and target

    def __len__(self) -> int:
        return len(self.y_delay)

    @property
    def origin_delay(self) -> np.ndarray:
        return self.X[:, FEATURE_NAMES.index("origin_delay_s")]

    @property
    def residual(self) -> np.ndarray:
        return self.y_delay - self.origin_delay

    def take(self, mask: np.ndarray) -> Samples:
        idx = np.flatnonzero(mask)
        return Samples(
            X=self.X[idx],
            y_delay=self.y_delay[idx],
            t0=self.t0[idx],
            target_s=self.target_s[idx],
            trip=self.trip[idx],
            trip_id=[self.trip_id[i] for i in idx],
            line=[self.line[i] for i in idx],
            stop_id=[self.stop_id[i] for i in idx],
            lag=self.lag[idx],
        )


def build_samples(events: Sequence[Event], lags: Sequence[int] = DEFAULT_LAGS) -> Samples:
    """Simulate a prediction at the moment each arrival became known.

    For every recorded arrival (the origin) whose trip has no later arrival known yet, each
    target stop ``lag`` stops ahead that has not been reached yet becomes a sample. Features see
    only arrivals known at that moment.
    """
    index = EventIndex(events)
    wanted = set(lags)
    rows: list[list[float]] = []
    y, t0s, target_times, trips, lag_col = [], [], [], [], []
    trip_ids, lines, stops = [], [], []
    trip_numbers: dict[str, int] = {}
    for trip_id, trip_events in index.by_trip.items():
        for origin in trip_events:
            t0 = origin.known_s
            known = known_trip_events(index, trip_id, t0)
            if known[-1] is not origin:
                continue  # a later stop was interpolated from the same fix: not the latest origin
            for target in trip_events:
                lag = target.stop_seq - origin.stop_seq
                if lag not in wanted or target.actual_s <= t0:
                    continue
                spec = TargetStop(
                    trip_id, target.stop_seq, target.stop_id, target.line, target.scheduled_s
                )
                rows.append(feature_vector(index, spec, origin, t0, known))
                y.append(target.delay_s)
                t0s.append(t0)
                target_times.append(target.actual_s)
                trips.append(trip_numbers.setdefault(trip_id, len(trip_numbers)))
                lag_col.append(lag)
                trip_ids.append(trip_id)
                lines.append(target.line)
                stops.append(target.stop_id)
    return Samples(
        X=np.array(rows, dtype=np.float64).reshape(-1, len(FEATURE_NAMES)),
        y_delay=np.array(y, dtype=np.float64),
        t0=np.array(t0s),
        target_s=np.array(target_times),
        trip=np.array(trips),
        trip_id=trip_ids,
        line=lines,
        stop_id=stops,
        lag=np.array(lag_col),
    )


FOLDS = ("train", "early_stop", "calibration", "test")


@dataclass(frozen=True)
class TimeFolds:
    """Four consecutive windows. A sample is in a fold only if its whole life, from the moment of
    prediction to the arrival it predicts, lies inside the window: nothing that is known when a
    test prediction is made was used to fit, tune or calibrate anything."""

    cuts: tuple[float, float, float, float, float]  # epoch seconds: start, 3 boundaries, end

    def assign(self, samples: Samples) -> np.ndarray:
        """Fold number (index into ``FOLDS``) per sample, -1 for samples straddling a boundary."""
        fold = np.full(len(samples), -1)
        for number in range(len(FOLDS)):
            start, end = self.cuts[number], self.cuts[number + 1]
            inside = (samples.t0 >= start) & (samples.target_s <= end)
            fold[inside & (fold < 0)] = number
        return fold


def time_folds(events: Sequence[Event], fractions=(0.68, 0.76, 0.84)) -> TimeFolds:
    """Boundaries at fixed fractions of the recorded time span (not of the sample count)."""
    start = min(event.known_s for event in events)
    end = max(event.actual_s for event in events)
    bounds = [start + (end - start) * fraction for fraction in fractions]
    return TimeFolds((start, *bounds, end))
