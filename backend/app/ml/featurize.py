"""Features of a delay prediction, computed from recorded stop arrivals only.

The same code builds training rows (``ml.dataset``) and live rows (``app.ml.live``), so a model
sees identical definitions at training and at prediction time.

A prediction is made at ``t0``, the moment the newest arrival of a trip became known: the
*origin* is that arrival, the *target* is a later stop of the same trip. An arrival becomes known
with the observation after the crossing (``actual_at + gap_s``); nothing that became known after
``t0`` is ever used.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..timeutil import TIMEZONE

NAN = math.nan

FEATURE_NAMES: tuple[str, ...] = (
    "stops_ahead",
    "origin_delay_s",
    "vehicle_delay_s",
    "vehicle_minus_origin_s",
    "prev_delay_change_s",
    "trend_s_per_min",
    "sched_run_s",
    "stop_seq",
    "hour_frac",
    "weekday",
    "is_tram",
    "line_recent_delay_s",
    "stop_recent_delay_s",
    "leader_delay_s",
    "headway_s",
)

TREND_WINDOW_S = 600
LINE_WINDOW_S = 900
STOP_WINDOW_S = 1800
LEADER_WINDOW_S = 3600


@dataclass(frozen=True, slots=True)
class Event:
    """One recorded stop arrival. Times are epoch seconds."""

    trip_id: str
    stop_seq: int
    stop_id: str
    line: str
    scheduled_s: float
    actual_s: float
    delay_s: int
    known_s: float


@dataclass(frozen=True, slots=True)
class TargetStop:
    trip_id: str
    stop_seq: int
    stop_id: str
    line: str
    scheduled_s: float


class _Series:
    """Events sorted by the moment they became known, with prefix sums of their delays."""

    __slots__ = ("events", "known", "prefix")

    def __init__(self, events: Sequence[Event]) -> None:
        self.events = sorted(events, key=lambda event: event.known_s)
        self.known = [event.known_s for event in self.events]
        self.prefix = [0]
        for event in self.events:
            self.prefix.append(self.prefix[-1] + event.delay_s)

    def window(self, start: float, end: float) -> tuple[int, int]:
        """Index range of events known in (start, end]."""
        return bisect_right(self.known, start), bisect_right(self.known, end)


class EventIndex:
    """Lookups over a set of recorded arrivals, by trip, line, stop and (line, stop)."""

    def __init__(self, events: Iterable[Event]) -> None:
        by_trip: dict[str, list[Event]] = defaultdict(list)
        by_line: dict[str, list[Event]] = defaultdict(list)
        by_stop: dict[str, list[Event]] = defaultdict(list)
        by_line_stop: dict[tuple[str, str], list[Event]] = defaultdict(list)
        for event in events:
            by_trip[event.trip_id].append(event)
            by_line[event.line].append(event)
            by_stop[event.stop_id].append(event)
            by_line_stop[(event.line, event.stop_id)].append(event)
        self.by_trip = {key: sorted(v, key=lambda e: e.stop_seq) for key, v in by_trip.items()}
        self._line = {key: _Series(v) for key, v in by_line.items()}
        self._stop = {key: _Series(v) for key, v in by_stop.items()}
        self._line_stop = {key: _Series(v) for key, v in by_line_stop.items()}

    def line_mean(self, line: str, t0: float, exclude_trip: str) -> float:
        series = self._line.get(line)
        if series is None:
            return NAN
        lo, hi = series.window(t0 - LINE_WINDOW_S, t0)
        total, count = series.prefix[hi] - series.prefix[lo], hi - lo
        for event in series.events[lo:hi]:
            if event.trip_id == exclude_trip:
                total -= event.delay_s
                count -= 1
        return total / count if count >= 2 else NAN

    def stop_mean(self, stop_id: str, t0: float, exclude_trip: str) -> float:
        series = self._stop.get(stop_id)
        if series is None:
            return NAN
        lo, hi = series.window(t0 - STOP_WINDOW_S, t0)
        total, count = series.prefix[hi] - series.prefix[lo], hi - lo
        for event in series.events[lo:hi]:
            if event.trip_id == exclude_trip:
                total -= event.delay_s
                count -= 1
        return total / count if count >= 1 else NAN

    def leader(self, target: TargetStop, t0: float) -> Event | None:
        """The latest-known earlier-scheduled arrival of another trip of the line at the stop."""
        series = self._line_stop.get((target.line, target.stop_id))
        if series is None:
            return None
        lo, hi = series.window(t0 - LEADER_WINDOW_S, t0)
        for event in reversed(series.events[lo:hi]):
            if event.trip_id != target.trip_id and event.scheduled_s < target.scheduled_s:
                return event if target.scheduled_s - event.scheduled_s <= LEADER_WINDOW_S else None
        return None


def is_tram(line: str) -> int:
    """Warsaw tram lines are numbered below 100; buses are 100 and up or carry a letter."""
    return int(line.isdigit() and int(line) < 100)


def trend(points: Sequence[tuple[float, int]]) -> float:
    """Least-squares slope of delay over time, in seconds of delay per minute."""
    if len(points) < 3:
        return NAN
    origin = points[0][0]
    xs = [(moment - origin) / 60 for moment, _ in points]
    ys = [delay for _, delay in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread < 1e-9:
        return NAN
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread


def known_trip_events(index: EventIndex, trip_id: str, t0: float) -> list[Event]:
    """Arrivals of the trip that were known at ``t0``, in stop order."""
    return [e for e in index.by_trip.get(trip_id, ()) if e.known_s <= t0]


def base_delay(values: Mapping[str, float]) -> float:
    """The delay the model adds its predicted change to: the vehicle's latest reported delay,
    or the delay at its latest recorded stop when no live delay is known."""
    live = values.get("vehicle_delay_s", NAN)
    return live if not math.isnan(live) else values["origin_delay_s"]


def feature_vector(
    index: EventIndex,
    target: TargetStop,
    origin: Event,
    t0: float,
    trip_known: Sequence[Event] | None = None,
    vehicle_delay_s: float = NAN,
) -> list[float]:
    """Feature values in ``FEATURE_NAMES`` order for predicting ``target`` from ``origin``.

    ``vehicle_delay_s`` is the delay the vehicle itself reported at ``t0`` (NaN if unknown); it is
    fresher than the delay at the last stop by up to one polling gap."""
    known = trip_known if trip_known is not None else known_trip_events(index, origin.trip_id, t0)
    earlier = [event for event in known if event.stop_seq < origin.stop_seq]
    change = origin.delay_s - earlier[-1].delay_s if earlier else NAN
    recent = [
        (event.actual_s, event.delay_s)
        for event in known
        if event.stop_seq <= origin.stop_seq and event.actual_s >= origin.actual_s - TREND_WINDOW_S
    ]
    leader = index.leader(target, t0)
    local = datetime.fromtimestamp(target.scheduled_s, TIMEZONE)
    return [
        float(target.stop_seq - origin.stop_seq),
        float(origin.delay_s),
        vehicle_delay_s,
        vehicle_delay_s - origin.delay_s,
        float(change),
        trend(sorted(recent)),
        target.scheduled_s - origin.scheduled_s,
        float(target.stop_seq),
        local.hour + local.minute / 60,
        float(local.weekday()),
        float(is_tram(target.line)),
        index.line_mean(target.line, t0, target.trip_id),
        index.stop_mean(target.stop_id, t0, target.trip_id),
        float(leader.delay_s) if leader else NAN,
        target.scheduled_s - leader.scheduled_s if leader else NAN,
    ]
