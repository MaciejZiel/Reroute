"""Model inputs of live targets, computed from recorded arrivals with the training definitions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..timeutil import trip_service_date
from .featurize import (
    FEATURE_NAMES,
    LEADER_WINDOW_S,
    MAX_ORIGIN_AGE_S,
    MAX_VEHICLE_AGE_S,
    Event,
    EventIndex,
    TargetStop,
    feature_vector,
    known_trip_events,
)

# Two queries rather than one OR: the trips come from the primary key, the recent rows from the
# arrival-time index; combined in one statement the planner rechecks both arms on every row of
# the lossy bitmap.
TRIP_SQL = text(
    """
    SELECT trip_id, stop_seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s
    FROM stop_arrival_events
    WHERE service_date = ANY(:dates) AND trip_id = ANY(:trips)
    """
)
RECENT_SQL = text(
    """
    SELECT trip_id, stop_seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s
    FROM stop_arrival_events
    WHERE actual_at >= :since AND actual_at <= :now
      AND (line = ANY(:lines) OR stop_id = ANY(:stops))
    """
)


def load_index(
    db: Session,
    trips: Sequence[str],
    lines: Sequence[str],
    stops: Sequence[str],
    now: datetime,
) -> EventIndex:
    dates = sorted({day for trip in trips if (day := trip_service_date(trip))})
    rows = [
        *db.execute(TRIP_SQL, {"dates": dates, "trips": list(trips)}).all(),
        *db.execute(
            RECENT_SQL,
            {
                "since": now - timedelta(seconds=LEADER_WINDOW_S + 60),
                "now": now,
                "lines": list(lines),
                "stops": list(stops),
            },
        ).all(),
    ]
    events = {}
    for trip_id, seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s in rows:
        events[(trip_id, seq)] = Event(
            trip_id=trip_id,
            stop_seq=seq,
            stop_id=stop_id,
            line=line,
            scheduled_s=scheduled_at.timestamp(),
            actual_s=actual_at.timestamp(),
            delay_s=delay_s,
            known_s=actual_at.timestamp() + gap_s,
        )
    return EventIndex(events.values())


# Why the model does not answer, for a short note to the reader.
PAUSE_NO_VEHICLE = "no_vehicle"  # no live delay at all: nothing to say beyond the timetable
PAUSE_VEHICLE_REPORT = "vehicle_report"  # the vehicle's last report is too old
PAUSE_LAST_STOP = "last_stop"  # the last recorded stop is too old
PAUSE_NO_STOP = "no_stop"  # no stop of the trip recorded yet


def model_inputs_with_reasons(
    index: EventIndex,
    targets: Sequence[TargetStop],
    now: datetime,
    vehicle_delays: Sequence[float | None],
    vehicle_ages_s: Sequence[float | None],
) -> list[tuple[dict[str, float] | None, str | None]]:
    """Feature values per target as of ``now``, or (None, reason) where the model must not
    answer: no live vehicle delay, or one older than ``MAX_VEHICLE_AGE_S``; no recorded stop of
    the trip, or the newest older than ``MAX_ORIGIN_AGE_S``; the target already passed (no
    reason). The caller then uses the baseline, because the model was never trained or tested in
    those situations."""
    t0 = now.timestamp()
    known_by_trip: dict[str, list[Event]] = {}
    result: list[tuple[dict[str, float] | None, str | None]] = []
    for number, target in enumerate(targets):
        live, age = vehicle_delays[number], vehicle_ages_s[number]
        if live is None or age is None:
            result.append((None, PAUSE_NO_VEHICLE))
            continue
        if age > MAX_VEHICLE_AGE_S:
            result.append((None, PAUSE_VEHICLE_REPORT))
            continue
        known = known_by_trip.setdefault(
            target.trip_id, known_trip_events(index, target.trip_id, t0)
        )
        if not known:
            result.append((None, PAUSE_NO_STOP))
        elif known[-1].stop_seq >= target.stop_seq:
            result.append((None, None))
        elif t0 - known[-1].known_s > MAX_ORIGIN_AGE_S:
            result.append((None, PAUSE_LAST_STOP))
        else:
            values = feature_vector(index, target, known[-1], t0, known, float(live), float(age))
            result.append((dict(zip(FEATURE_NAMES, values, strict=True)), None))
    return result


def model_inputs(
    index: EventIndex,
    targets: Sequence[TargetStop],
    now: datetime,
    vehicle_delays: Sequence[float | None],
    vehicle_ages_s: Sequence[float | None],
) -> list[dict[str, float] | None]:
    """The inputs only, ``None`` where the model must not answer (see the function above)."""
    found = model_inputs_with_reasons(index, targets, now, vehicle_delays, vehicle_ages_s)
    return [values for values, _ in found]
