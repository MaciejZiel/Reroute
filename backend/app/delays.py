"""Estimate per-line delays by comparing live vehicle positions with the timetable.

Each live vehicle reports the GTFS trip it is running. The position is projected onto the
straight lines between that trip's consecutive stops; the scheduled time at that point is
interpolated between the two stop departures, and the delay is the feed timestamp minus
that scheduled time. Vehicles far from their trip, waiting at the first stop or with
implausible results are skipped rather than guessed.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .data_sources import LiveVehicle, LiveVehicleSnapshot
from .models import DelaySnapshot, Stop, StopPattern, TripSchedule

TIMEZONE = ZoneInfo("Europe/Warsaw")
MAX_OFF_ROUTE_M = 250
MIN_DELAY_S = -15 * 60
MAX_DELAY_S = 90 * 60
EARLY_LIMIT_S = -60
LATE_LIMIT_S = 180
SNAPSHOT_INTERVAL_S = 60
SNAPSHOT_RETENTION = timedelta(days=7)


@dataclass(frozen=True)
class ScheduledStop:
    latitude: float
    longitude: float
    departure_s: int


@dataclass(frozen=True)
class VehicleDelay:
    vehicle_id: str
    line: str
    mode: str
    delay_seconds: int


@dataclass(frozen=True)
class LineDelay:
    line: str
    mode: str
    vehicles: int
    mean_delay_seconds: int
    median_delay_seconds: int
    early: int
    on_time: int
    late: int


def scheduled_seconds_at(
    latitude: float, longitude: float, stops: Sequence[ScheduledStop]
) -> float | None:
    """Scheduled time (seconds after service-day midnight) at the vehicle's position."""
    if len(stops) < 2:
        return None
    scale_x = 111_320 * math.cos(math.radians(latitude))
    scale_y = 110_540
    best: tuple[float, int, float] | None = None
    for index, (start, end) in enumerate(zip(stops, stops[1:], strict=False)):
        ax, ay = (start.longitude - longitude) * scale_x, (start.latitude - latitude) * scale_y
        bx, by = (end.longitude - longitude) * scale_x, (end.latitude - latitude) * scale_y
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        fraction = 0.0 if length == 0 else min(max(-(ax * dx + ay * dy) / length, 0.0), 1.0)
        distance = math.hypot(ax + fraction * dx, ay + fraction * dy)
        if best is None or distance < best[0]:
            best = (distance, index, fraction)
    distance, index, fraction = best  # type: ignore[misc]
    if distance > MAX_OFF_ROUTE_M:
        return None
    if index == 0 and fraction == 0.0:
        return None  # standing at the first stop: layover, not a delay
    start, end = stops[index], stops[index + 1]
    return start.departure_s + fraction * (end.departure_s - start.departure_s)


def delay_seconds(observed_at: datetime, scheduled_s: float) -> int | None:
    """Delay against the closest service day; GTFS times count from noon minus 12 hours."""
    local_day = observed_at.astimezone(TIMEZONE).date()
    candidates = []
    for day in (local_day - timedelta(days=1), local_day):
        origin = datetime.combine(day, time(12), TIMEZONE) - timedelta(hours=12)
        candidates.append((observed_at - origin).total_seconds() - scheduled_s)
    delay = min(candidates, key=abs)
    if not MIN_DELAY_S <= delay <= MAX_DELAY_S:
        return None
    return round(delay)


def summarise(delays: Iterable[VehicleDelay]) -> list[LineDelay]:
    by_line: dict[tuple[str, str], list[int]] = defaultdict(list)
    for item in delays:
        by_line[(item.line, item.mode)].append(item.delay_seconds)
    summaries = []
    for (line, mode), values in by_line.items():
        early = sum(value < EARLY_LIMIT_S for value in values)
        late = sum(value > LATE_LIMIT_S for value in values)
        summaries.append(
            LineDelay(
                line=line,
                mode=mode,
                vehicles=len(values),
                mean_delay_seconds=round(mean(values)),
                median_delay_seconds=round(median(values)),
                early=early,
                on_time=len(values) - early - late,
                late=late,
            )
        )
    return sorted(summaries, key=lambda item: (-item.vehicles, item.line))


def estimate_delays(
    vehicles: Iterable[LiveVehicle],
    schedules: dict[str, Sequence[ScheduledStop]],
) -> list[VehicleDelay]:
    results = []
    for vehicle in vehicles:
        stops = schedules.get(vehicle.trip_id)
        if not stops:
            continue
        scheduled = scheduled_seconds_at(vehicle.latitude, vehicle.longitude, stops)
        if scheduled is None:
            continue
        delay = delay_seconds(vehicle.observed_at, scheduled)
        if delay is not None:
            results.append(VehicleDelay(vehicle.id, vehicle.line, vehicle.mode, delay))
    return results


def load_schedules(session: Session, trip_ids: Iterable[str]) -> dict[str, list[ScheduledStop]]:
    """Fetch the timetables of the trips currently in the live feed."""
    wanted = sorted({trip_id for trip_id in trip_ids if trip_id})
    if not wanted:
        return {}
    rows = []
    for start in range(0, len(wanted), 1000):
        rows.extend(
            session.execute(
                select(TripSchedule.trip_id, TripSchedule.departures, StopPattern.stop_ids)
                .join(StopPattern, StopPattern.id == TripSchedule.pattern_id)
                .where(TripSchedule.trip_id.in_(wanted[start : start + 1000]))
            ).all()
        )
    stop_ids = {stop_id for _, _, pattern in rows for stop_id in pattern.split(",")}
    coordinates = {
        stop_id: (latitude, longitude)
        for stop_id, latitude, longitude in session.execute(
            select(Stop.id, Stop.latitude, Stop.longitude).where(Stop.id.in_(stop_ids))
        ).all()
    }
    schedules = {}
    for trip_id, departures, pattern in rows:
        stops = [
            ScheduledStop(*coordinates[stop_id], int(departure))
            for stop_id, departure in zip(pattern.split(","), departures.split(","), strict=False)
            if stop_id in coordinates
        ]
        schedules[trip_id] = stops
    return schedules


_snapshot_lock = threading.Lock()
_last_snapshot: tuple[datetime | None, list[VehicleDelay]] = (None, [])
_last_stored_at: datetime | None = None


def delays_for_snapshot(session: Session, snapshot: LiveVehicleSnapshot) -> list[VehicleDelay]:
    """Delays of one live snapshot, computed once and stored as per-line rows every minute."""
    global _last_snapshot, _last_stored_at
    if not snapshot.vehicles or snapshot.observed_at is None:
        return []
    with _snapshot_lock:
        if _last_snapshot[0] == snapshot.observed_at:
            return _last_snapshot[1]
        schedules = load_schedules(session, (vehicle.trip_id for vehicle in snapshot.vehicles))
        delays = estimate_delays(snapshot.vehicles, schedules)
        _last_snapshot = (snapshot.observed_at, delays)
        if delays and (
            _last_stored_at is None
            or (snapshot.observed_at - _last_stored_at).total_seconds() >= SNAPSHOT_INTERVAL_S
        ):
            store_snapshot(session, snapshot.observed_at, summarise(delays))
            _last_stored_at = snapshot.observed_at
        return delays


def store_snapshot(session: Session, observed_at: datetime, lines: Iterable[LineDelay]) -> None:
    session.add_all(
        DelaySnapshot(
            observed_at=observed_at,
            line=line.line,
            mode=line.mode,
            vehicles=line.vehicles,
            mean_delay_seconds=line.mean_delay_seconds,
            median_delay_seconds=line.median_delay_seconds,
            early=line.early,
            on_time=line.on_time,
            late=line.late,
        )
        for line in lines
    )
    session.execute(
        delete(DelaySnapshot).where(
            DelaySnapshot.observed_at < datetime.now(UTC) - SNAPSHOT_RETENTION
        )
    )
    session.commit()


def demo_line_delays(
    now: datetime, lines: Sequence[tuple[str, str]], points: int = 48
) -> list[DelaySnapshot]:
    """Deterministic fictional history for the demo network, clearly labelled as demo."""
    history = []
    for step in range(points):
        observed_at = (now - timedelta(minutes=2.5 * (points - 1 - step))).replace(microsecond=0)
        for index, (line, mode) in enumerate(lines):
            phase = step / 7 + index * 1.9
            base = 40 + index * 55 + 110 * math.sin(phase) + 40 * math.sin(phase * 2.3)
            vehicles = 4 + (index + step) % 3
            late = max(0, min(vehicles, round(vehicles * max(base, 0) / 400)))
            early = 1 if base < -20 else 0
            history.append(
                DelaySnapshot(
                    observed_at=observed_at,
                    line=line,
                    mode=mode,
                    vehicles=vehicles,
                    mean_delay_seconds=round(base),
                    median_delay_seconds=round(base * 0.9),
                    early=early,
                    on_time=vehicles - late - early,
                    late=late,
                )
            )
    return history
