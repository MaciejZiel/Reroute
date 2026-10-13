"""Model inputs of live targets, computed from recorded arrivals with the training definitions."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..timeutil import trip_service_date
from .featurize import (
    FEATURE_NAMES,
    LEADER_WINDOW_S,
    Event,
    EventIndex,
    TargetStop,
    feature_vector,
    known_trip_events,
)

# Arrivals of the targeted trips (primary-key lookup) and everything recent on their lines and
# stops (the actual_at index prunes the partition scan).
RECENT_SQL = text(
    """
    SELECT trip_id, stop_seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s
    FROM stop_arrival_events
    WHERE (service_date = ANY(:dates) AND trip_id = ANY(:trips))
       OR (actual_at >= :since AND actual_at <= :now
           AND (line = ANY(:lines) OR stop_id = ANY(:stops)))
    """
)


def load_index(
    db: Session,
    trips: Sequence[str],
    lines: Sequence[str],
    stops: Sequence[str],
    now: datetime,
) -> EventIndex:
    rows = db.execute(
        RECENT_SQL,
        {
            "dates": sorted({day for trip in trips if (day := trip_service_date(trip))}),
            "trips": list(trips),
            "since": now - timedelta(seconds=LEADER_WINDOW_S + 60),
            "now": now,
            "lines": list(lines),
            "stops": list(stops),
        },
    ).all()
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


def model_inputs(
    index: EventIndex,
    targets: Sequence[TargetStop],
    now: datetime,
    vehicle_delays: Sequence[float | None] | None = None,
) -> list[dict[str, float] | None]:
    """Feature values per target as of ``now``; None when the trip has no recorded stop yet or
    the target stop has already been passed."""
    t0 = now.timestamp()
    known_by_trip: dict[str, list[Event]] = {}
    result: list[dict[str, float] | None] = []
    for number, target in enumerate(targets):
        live = vehicle_delays[number] if vehicle_delays else None
        known = known_by_trip.setdefault(
            target.trip_id, known_trip_events(index, target.trip_id, t0)
        )
        if not known or known[-1].stop_seq >= target.stop_seq:
            result.append(None)
            continue
        values = feature_vector(
            index, target, known[-1], t0, known, math.nan if live is None else float(live)
        )
        result.append(dict(zip(FEATURE_NAMES, values, strict=True)))
    return result
