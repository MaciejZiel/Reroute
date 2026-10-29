"""Training samples from recorded arrivals, and the time-ordered folds they are split into."""

from __future__ import annotations

import csv
import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from app.ml.featurize import (
    FEATURE_NAMES,
    MAX_ORIGIN_AGE_S,
    MAX_VEHICLE_AGE_S,
    Event,
    EventIndex,
    TargetStop,
    feature_vector,
    known_trip_events,
)

DEFAULT_LAGS = (1, 2, 3, 4, 6, 8, 12)
CLUSTER_SECONDS = 900  # bootstrap clusters: one line in one 15-minute block
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

    def clusters(self) -> np.ndarray:
        """Cluster key of each sample for the bootstrap: its line and the 15-minute block of the
        prediction moment. Samples of one line in one block share the line's recent delays and the
        traffic of that stretch of the evening, so they are not independent draws; resampling per
        trip would count them as if they were."""
        block = (self.t0 // CLUSTER_SECONDS).astype(np.int64)
        return np.array([f"{line}|{b}" for line, b in zip(self.line, block, strict=True)])

    @property
    def origin_delay(self) -> np.ndarray:
        return self.X[:, FEATURE_NAMES.index("origin_delay_s")]

    @property
    def base_delay(self) -> np.ndarray:
        """What persistence predicts and what the model adds its change to: the vehicle's own
        latest delay where one was reported, else the delay at the last recorded stop."""
        live = self.X[:, FEATURE_NAMES.index("vehicle_delay_s")]
        return np.where(np.isnan(live), self.origin_delay, live)

    @property
    def residual(self) -> np.ndarray:
        return self.y_delay - self.base_delay

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


class Observations:
    """Delays the vehicles themselves reported, to look up the latest one at a moment in time."""

    MAX_AGE_S = MAX_VEHICLE_AGE_S

    def __init__(self, rows: dict[str, tuple[list[float], list[int]]]) -> None:
        self.rows = rows

    @property
    def newest(self) -> float | None:
        """Epoch seconds of the newest report held, if any."""
        return max((times[-1] for times, _ in self.rows.values()), default=None)

    @classmethod
    def load(cls, path: Path, until: float | None = None) -> Observations:
        """Read the CSV written by ``ml/pull.sh`` (trip_id, observed_at, delay_s); with ``until``
        (epoch seconds) only reports made up to then, as at the time the data was pulled."""
        rows: dict[str, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                observed = datetime.fromisoformat(row["observed_at"]).timestamp()
                if until is not None and observed > until:
                    continue
                times, delays = rows[row["trip_id"]]
                times.append(observed)
                delays.append(int(row["delay_s"]))
        return cls(dict(rows))

    def latest(self, trip_id: str, moment: float) -> tuple[float, float]:
        """(delay, age in seconds) of the newest observation of the trip at or before ``moment``;
        (NaN, NaN) if none is recent enough to be served live."""
        series = self.rows.get(trip_id)
        if series is None:
            return math.nan, math.nan
        times, delays = series
        i = bisect_right(times, moment) - 1
        if i < 0 or moment - times[i] > self.MAX_AGE_S:
            return math.nan, math.nan
        return float(delays[i]), moment - times[i]


MOMENT_EVERY_S = 60.0  # on average one simulated request per minute of a trip's life


def _moments(
    index: EventIndex,
    trip_id: str,
    observations: Observations | None,
    rng,
    every_s: float = MOMENT_EVERY_S,
):
    """Moments at which a prediction for this trip is simulated.

    With observations: uniformly random times over the trip's observed life, on average one per
    ``every_s`` seconds, so the age of the newest vehicle report is what a live request sees (long
    gaps between reports are as likely to be hit as they are in reality). Without: the instant
    each arrival became known."""
    if observations is None:
        return sorted({event.known_s for event in index.by_trip[trip_id]})
    times = observations.rows.get(trip_id, ([], []))[0]
    if len(times) < 2:
        return []
    count = max(int((times[-1] - times[0]) / every_s), 1)
    return sorted(float(m) for m in rng.uniform(times[0], times[-1], count))


def build_samples(
    events: Sequence[Event],
    lags: Sequence[int] = DEFAULT_LAGS,
    observations: Observations | None = None,
    seed: int = 1,
    moment_every_s: float = MOMENT_EVERY_S,
) -> Samples:
    """Simulate predictions the way the server makes them.

    At each moment the origin is the newest arrival of the trip known by then. Moments where the
    server would not use the model (origin older than ``MAX_ORIGIN_AGE_S``, or, when observations
    are given, no vehicle delay fresher than ``MAX_VEHICLE_AGE_S``) are skipped. Each target stop
    ``lag`` stops after the origin that has not been reached yet becomes a sample. Features see
    only arrivals known at the moment.
    """
    index = EventIndex(events)
    wanted = set(lags)
    rng = np.random.default_rng(seed)
    # Columns are turned into arrays one trip at a time: a list of millions of Python floats
    # takes several times the memory of the same numbers in an array.
    blocks: dict[str, list[np.ndarray]] = {
        name: [] for name in ("X", "y", "t0", "target", "trip", "lag")
    }
    trip_ids, lines, stops = [], [], []
    trip_numbers: dict[str, int] = {}
    for trip_id, trip_events in index.by_trip.items():
        rows: list[list[float]] = []
        y, t0s, target_times, trips, lag_col = [], [], [], [], []
        for moment in _moments(index, trip_id, observations, rng, moment_every_s):
            known = known_trip_events(index, trip_id, moment)
            if not known:
                continue
            origin = known[-1]
            if moment - origin.known_s > MAX_ORIGIN_AGE_S:
                continue
            live, age = observations.latest(trip_id, moment) if observations else (math.nan,) * 2
            if observations is not None and math.isnan(live):
                continue
            for target in trip_events:
                lag = target.stop_seq - origin.stop_seq
                if lag not in wanted or target.actual_s <= moment:
                    continue
                spec = TargetStop(
                    trip_id, target.stop_seq, target.stop_id, target.line, target.scheduled_s
                )
                rows.append(feature_vector(index, spec, origin, moment, known, live, age))
                y.append(target.delay_s)
                t0s.append(moment)
                target_times.append(target.actual_s)
                trips.append(trip_numbers.setdefault(trip_id, len(trip_numbers)))
                lag_col.append(lag)
                trip_ids.append(trip_id)
                lines.append(target.line)
                stops.append(target.stop_id)
        if rows:
            blocks["X"].append(np.array(rows, dtype=np.float64))
            blocks["y"].append(np.array(y, dtype=np.float64))
            blocks["t0"].append(np.array(t0s))
            blocks["target"].append(np.array(target_times))
            blocks["trip"].append(np.array(trips))
            blocks["lag"].append(np.array(lag_col))

    def column(name: str) -> np.ndarray:
        return np.concatenate(blocks.pop(name)) if blocks[name] else np.array([])

    if not blocks["X"]:
        blocks["X"].append(np.empty((0, len(FEATURE_NAMES))))
    return Samples(
        X=column("X"),
        y_delay=column("y"),
        t0=column("t0"),
        target_s=column("target"),
        trip=column("trip"),
        trip_id=trip_ids,
        line=lines,
        stop_id=stops,
        lag=column("lag"),
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
