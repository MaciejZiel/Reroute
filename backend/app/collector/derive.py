"""Turn one decoded vehicle feed into observation rows and stop arrival events.

Everything here is a pure function of (feed, previous per-vehicle state, timetable lookup):
no network, no database and no clock, so the live loop and the backfill from raw snapshots
run exactly the same code.

Progress along a trip is measured in stop-index units: 3.4 means 40 % of the way from the
fourth stop to the fifth. A vehicle is projected onto the straight lines between consecutive
stops; its scheduled time is interpolated the same way, and the delay is the observation time
minus that scheduled instant (DST-safe, see ``timeutil``). Stop positions ("stop_seq") are
1-based indexes into the trip, not the GTFS stop_sequence values.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..timeutil import service_instant, trip_service_date

MAX_OFF_ROUTE_M = 250
MIN_DELAY_S = -15 * 60
MAX_DELAY_S = 90 * 60
LAYOVER_PROGRESS = 0.02
# Observations further apart than this do not produce arrival events: the vehicle may have
# been out of range, and interpolating across a long gap would invent times.
MAX_EVENT_GAP_S = 600
MAX_EVENT_STOPS = 10
WINDOW_AHEAD_SEGMENTS = 12


class MatchStatus(enum.IntEnum):
    OK = 0
    NO_TRIP = 1
    TRIP_UNKNOWN = 2
    OFF_ROUTE = 3
    LAYOVER = 4
    IMPLAUSIBLE = 5


@dataclass(frozen=True, slots=True)
class TripTimetable:
    """Scheduled stops of one trip, in order; a stop's index is its position in the trip."""

    trip_id: str
    route_id: str
    line: str
    mode: str
    service_date: date | None
    stop_ids: tuple[str, ...]
    latitudes: tuple[float, ...]
    longitudes: tuple[float, ...]
    departures: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Observation:
    """One vehicle position taken from the feed."""

    vehicle_id: str
    label: str | None
    trip_id: str | None
    latitude: float
    longitude: float
    bearing: float | None
    speed: float | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class Projection:
    progress: float
    distance_m: float
    scheduled_s: float


@dataclass(slots=True)
class VehicleState:
    """What the previous observation of a vehicle told us; progress is None when unmatched."""

    trip_id: str
    observed_at: datetime
    progress: float | None
    emitted_upto: int


@dataclass(slots=True)
class PollResult:
    observations: list[dict[str, Any]] = field(default_factory=list)
    arrivals: list[dict[str, Any]] = field(default_factory=list)
    skipped_duplicates: int = 0


Timetables = Callable[[Iterable[str]], dict[str, TripTimetable]]
LineResolver = Callable[[Observation], tuple[str, str, str | None]]


def project(
    latitude: float,
    longitude: float,
    timetable: TripTimetable,
    last_progress: float | None = None,
) -> Projection | None:
    """Project a position onto the trip's stop-to-stop lines.

    With a known previous progress the search starts in a window just ahead of it, which keeps a
    line that passes the same stop twice (a loop) from jumping to the wrong visit; it widens to
    the whole trip when nothing in the window is close enough.
    """
    count = len(timetable.stop_ids)
    if count < 2:
        return None
    found = None
    if last_progress is not None:
        first = max(int(last_progress) - 1, 0)
        window = range(first, min(first + WINDOW_AHEAD_SEGMENTS, count - 1))
        found = _best_segment(latitude, longitude, timetable, window, last_progress)
    if found is None or found[0] > MAX_OFF_ROUTE_M:
        found = _best_segment(latitude, longitude, timetable, range(count - 1), last_progress)
    if found is None:
        return None
    distance, index, fraction = found
    start, end = timetable.departures[index], timetable.departures[index + 1]
    return Projection(index + fraction, distance, start + fraction * (end - start))


def _best_segment(
    latitude: float,
    longitude: float,
    timetable: TripTimetable,
    segments: Iterable[int],
    anchor: float | None = None,
) -> tuple[float, int, float] | None:
    """Nearest segment as (distance in m, index, fraction).

    Given the previous progress, going backwards costs 30 m per stop and going ahead 5 m per
    stop, so of two equally near visits of a looping line the one just ahead wins.
    """
    scale_x = 111_320 * math.cos(math.radians(latitude))
    scale_y = 110_540
    lats, lons = timetable.latitudes, timetable.longitudes
    best: tuple[float, int, float] | None = None
    best_cost = math.inf
    for index in segments:
        ax, ay = (lons[index] - longitude) * scale_x, (lats[index] - latitude) * scale_y
        bx, by = (lons[index + 1] - longitude) * scale_x, (lats[index + 1] - latitude) * scale_y
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        fraction = 0.0 if length == 0 else min(max(-(ax * dx + ay * dy) / length, 0.0), 1.0)
        distance = math.hypot(ax + fraction * dx, ay + fraction * dy)
        cost = distance
        if anchor is not None:
            moved = index + fraction - anchor
            cost += 5 * moved if moved >= 0 else -30 * moved
        if cost < best_cost:
            best, best_cost = (distance, index, fraction), cost
    return best


def derive_poll(
    observations: Sequence[Observation],
    timetables: Timetables,
    state: dict[str, VehicleState],
    feed_at: datetime | None,
    source: int = 0,
    resolve_line: LineResolver | None = None,
) -> PollResult:
    """Observation rows and arrival events of one feed; ``state`` is updated in place.

    ``resolve_line`` maps an observation without a usable trip to ``(line, mode, route_id)``.
    """
    result = PollResult()
    wanted = {obs.trip_id for obs in observations if obs.trip_id}
    known = timetables(wanted) if wanted else {}
    for obs in observations:
        previous = state.get(obs.vehicle_id)
        if previous is not None and obs.observed_at <= previous.observed_at:
            result.skipped_duplicates += 1
            continue
        timetable = known.get(obs.trip_id) if obs.trip_id else None
        row = _observation_row(obs, timetable, previous, feed_at, source, resolve_line)
        result.observations.append(row)
        progress = row.pop("_progress")
        if timetable is not None and progress is not None:
            arrivals, emitted = _arrivals(obs, timetable, previous, progress)
            result.arrivals.extend(arrivals)
            state[obs.vehicle_id] = VehicleState(
                timetable.trip_id, obs.observed_at, progress, emitted
            )
        elif timetable is not None and previous is not None and previous.trip_id == obs.trip_id:
            # An off-route or implausible fix: keep the progress so the next good fix continues it.
            previous.observed_at = obs.observed_at
        else:
            state[obs.vehicle_id] = VehicleState(obs.trip_id or "", obs.observed_at, None, -1)
    return result


def _observation_row(
    obs: Observation,
    timetable: TripTimetable | None,
    previous: VehicleState | None,
    feed_at: datetime | None,
    source: int,
    resolve_line: LineResolver | None,
) -> dict[str, Any]:
    line, mode, route_id = resolve_line(obs) if resolve_line else ("?", "bus", None)
    row: dict[str, Any] = {
        "observed_at": obs.observed_at,
        "feed_at": feed_at,
        "vehicle_id": obs.vehicle_id,
        "label": obs.label,
        "trip_id": obs.trip_id or None,
        "service_date": None,
        "route_id": route_id,
        "line": line,
        "mode": mode,
        "lat": obs.latitude,
        "lon": obs.longitude,
        "bearing": round(obs.bearing) % 360 if obs.bearing is not None else None,
        "speed": obs.speed,
        "next_stop_seq": None,
        "next_stop_id": None,
        "scheduled_s": None,
        "delay_s": None,
        "match_status": int(MatchStatus.NO_TRIP),
        "source": source,
        "_progress": None,
    }
    if not obs.trip_id:
        return row
    row["service_date"] = trip_service_date(obs.trip_id)
    if timetable is None:
        row["match_status"] = int(MatchStatus.TRIP_UNKNOWN)
        return row
    row.update(
        line=timetable.line,
        mode=timetable.mode,
        route_id=timetable.route_id,
        service_date=timetable.service_date or row["service_date"],
    )
    same_trip = previous is not None and previous.trip_id == timetable.trip_id
    projection = project(
        obs.latitude, obs.longitude, timetable, previous.progress if same_trip else None
    )
    if projection is None:
        row["match_status"] = int(MatchStatus.TRIP_UNKNOWN)
        return row
    if projection.distance_m > MAX_OFF_ROUTE_M:
        row["match_status"] = int(MatchStatus.OFF_ROUTE)
        return row
    # Waiting at the first stop (layover): that stop is still ahead of the vehicle.
    layover = projection.progress < LAYOVER_PROGRESS
    next_index = 0 if layover else min(int(projection.progress) + 1, len(timetable.stop_ids) - 1)
    row["next_stop_seq"] = next_index + 1
    row["next_stop_id"] = timetable.stop_ids[next_index]
    row["scheduled_s"] = round(projection.scheduled_s)
    if layover:
        row["match_status"] = int(MatchStatus.LAYOVER)
        row["_progress"] = projection.progress
        return row
    if timetable.service_date is None:
        row["match_status"] = int(MatchStatus.TRIP_UNKNOWN)
        return row
    scheduled_at = service_instant(timetable.service_date, round(projection.scheduled_s))
    delay = round((obs.observed_at - scheduled_at).total_seconds())
    if not MIN_DELAY_S <= delay <= MAX_DELAY_S:
        row["match_status"] = int(MatchStatus.IMPLAUSIBLE)
        return row
    row["delay_s"] = delay
    row["match_status"] = int(MatchStatus.OK)
    row["_progress"] = projection.progress
    return row


def _arrivals(
    obs: Observation,
    timetable: TripTimetable,
    previous: VehicleState | None,
    progress: float,
) -> tuple[list[dict[str, Any]], int]:
    """Stops passed since the previous observation, with interpolated times.

    Returns the events and the highest stop index already accounted for. The departure from
    the first stop is never an event: its time is hidden inside the layover.
    """
    if previous is None or previous.trip_id != timetable.trip_id or previous.progress is None:
        return [], int(progress)
    advanced = progress - previous.progress
    if advanced <= 0 or timetable.service_date is None:
        return [], previous.emitted_upto
    gap = (obs.observed_at - previous.observed_at).total_seconds()
    emitted = max(previous.emitted_upto, int(progress))
    crossed = range(
        max(int(previous.progress) + 1, previous.emitted_upto + 1, 1), int(progress) + 1
    )
    if not crossed or gap > MAX_EVENT_GAP_S or len(crossed) > MAX_EVENT_STOPS:
        return [], emitted
    events = []
    for stop_index in crossed:
        share = (stop_index - previous.progress) / advanced
        actual_at = previous.observed_at + timedelta(seconds=gap * share)
        scheduled_at = service_instant(timetable.service_date, timetable.departures[stop_index])
        delay = round((actual_at - scheduled_at).total_seconds())
        if not MIN_DELAY_S <= delay <= MAX_DELAY_S:
            continue
        events.append(
            {
                "service_date": timetable.service_date,
                "trip_id": timetable.trip_id,
                "stop_seq": stop_index + 1,
                "stop_id": timetable.stop_ids[stop_index],
                "line": timetable.line,
                "scheduled_at": scheduled_at,
                "actual_at": actual_at,
                "delay_s": delay,
                "gap_s": round(gap),
            }
        )
    return events, emitted
