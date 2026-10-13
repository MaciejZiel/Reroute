"""Features for delay prediction, built from live positions and recorded history.

Two entry points share the feature definitions:

* ``live_feature_rows``: one row per (trip, upcoming stop) for the prediction endpoint;
* ``export_training_rows``: one row per recorded stop arrival, using only earlier stops of the
  same trip that were already known when the vehicle reached the target stop, so a model
  trained on the export sees at training time what it will see at prediction time.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from .collector import tables
from .timeutil import TIMEZONE

TREND_WINDOW = timedelta(minutes=10)
ROUTE_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class FeatureRow:
    trip_id: str
    line: str
    stop_id: str
    stop_seq: int
    scheduled_at: datetime
    stops_remaining: int  # stops between the vehicle and this one; 0 when it is the next stop
    current_delay_s: int | None
    delay_trend_s_per_min: float | None
    previous_stop_delay_s: int | None
    hour_local: int
    weekday: int
    route_mean_delay_s: float | None
    active_alerts: int
    # Inputs of the trained model by name; None when the trip has no recorded stop yet.
    model_inputs: dict[str, float] | None = None
    # Why model_inputs is None despite a model being in use (see app.ml.live.PAUSE_*), or None.
    model_pause: str | None = None


@dataclass(frozen=True)
class Target:
    """A stop a trip will call at, with what is known about the vehicle running it."""

    trip_id: str
    line: str
    route_id: str
    stop_id: str
    stop_seq: int
    scheduled_at: datetime
    service_date: date | None
    vehicle_id: str | None
    current_delay_s: int | None
    next_stop_seq: int | None
    # Seconds since the vehicle's last report; the model is not used with an old one.
    vehicle_age_s: float | None = None


def slope_per_minute(points: Sequence[tuple[datetime, int]]) -> float | None:
    """Least-squares slope of delay over time, in seconds of delay per minute."""
    if len(points) < 3:
        return None
    origin = points[0][0]
    xs = [(moment - origin).total_seconds() / 60 for moment, _ in points]
    ys = [delay for _, delay in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread < 1e-9:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread


def live_feature_rows(
    db: Session,
    targets: Sequence[Target],
    now: datetime,
    with_history: bool = True,
    with_model_inputs: bool = False,
) -> list[FeatureRow]:
    trips = sorted({target.trip_id for target in targets})
    vehicles = sorted({target.vehicle_id for target in targets if target.vehicle_id})
    lines = sorted({target.line for target in targets})
    trends = _trends(db, vehicles, now) if with_history else {}
    previous = _previous_delays(db, targets) if with_history and trips else {}
    route_delay = _route_delays(db, lines, now) if with_history else {}
    alerts = _alert_counts(db, sorted({target.route_id for target in targets}), now)
    rows = []
    for target in targets:
        local = target.scheduled_at.astimezone(TIMEZONE)
        remaining = (
            max(target.stop_seq - target.next_stop_seq, 0)
            if target.next_stop_seq is not None
            else 0
        )
        rows.append(
            FeatureRow(
                trip_id=target.trip_id,
                line=target.line,
                stop_id=target.stop_id,
                stop_seq=target.stop_seq,
                scheduled_at=target.scheduled_at,
                stops_remaining=remaining,
                current_delay_s=target.current_delay_s,
                delay_trend_s_per_min=trends.get(target.vehicle_id or ""),
                previous_stop_delay_s=previous.get((target.trip_id, target.stop_seq)),
                hour_local=local.hour,
                weekday=local.weekday(),
                route_mean_delay_s=route_delay.get(target.line),
                active_alerts=alerts.get(target.route_id, 0),
            )
        )
    if with_model_inputs and targets:
        rows = _with_model_inputs(db, rows, targets, now)
    return rows


def _with_model_inputs(
    db: Session, rows: list[FeatureRow], targets: Sequence[Target], now: datetime
) -> list[FeatureRow]:
    from .ml.featurize import TargetStop
    from .ml.live import PAUSE_NO_VEHICLE, load_index, model_inputs_with_reasons

    if not any(t.current_delay_s is not None for t in targets):
        # no live vehicle among them: the baseline answers all, nothing to load
        return [replace(row, model_pause=PAUSE_NO_VEHICLE) for row in rows]
    specs = [
        TargetStop(t.trip_id, t.stop_seq, t.stop_id, t.line, t.scheduled_at.timestamp())
        for t in targets
    ]
    index = load_index(
        db,
        sorted({spec.trip_id for spec in specs}),
        sorted({spec.line for spec in specs}),
        sorted({spec.stop_id for spec in specs}),
        now,
    )
    found = model_inputs_with_reasons(
        index,
        specs,
        now,
        [t.current_delay_s for t in targets],
        [t.vehicle_age_s for t in targets],
    )
    return [
        replace(row, model_inputs=inputs, model_pause=pause)
        for row, (inputs, pause) in zip(rows, found, strict=True)
    ]


def _trends(db: Session, vehicle_ids: list[str], now: datetime) -> dict[str, float]:
    if not vehicle_ids:
        return {}
    obs = tables.vehicle_observations
    found: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for vehicle_id, observed_at, delay in db.execute(
        select(obs.c.vehicle_id, obs.c.observed_at, obs.c.delay_s)
        .where(
            obs.c.observed_at >= now - TREND_WINDOW,
            obs.c.vehicle_id.in_(vehicle_ids),
            obs.c.delay_s.is_not(None),
        )
        .order_by(obs.c.observed_at)
    ):
        found[vehicle_id].append((observed_at, delay))
    return {
        vehicle_id: trend
        for vehicle_id, points in found.items()
        if (trend := slope_per_minute(points)) is not None
    }


def _previous_delays(db: Session, targets: Sequence[Target]) -> dict[tuple[str, int], int]:
    """Delay at the latest recorded stop before each target stop of the same trip."""
    events = tables.stop_arrival_events
    dated = {(target.service_date, target.trip_id) for target in targets if target.service_date}
    if not dated:
        return {}
    by_trip: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for trip_id, stop_seq, delay in db.execute(
        select(events.c.trip_id, events.c.stop_seq, events.c.delay_s).where(
            events.c.service_date.in_({d for d, _ in dated}),
            events.c.trip_id.in_({t for _, t in dated}),
        )
    ):
        by_trip[trip_id].append((stop_seq, delay))
    result = {}
    for target in targets:
        earlier = [item for item in by_trip.get(target.trip_id, []) if item[0] < target.stop_seq]
        if earlier:
            result[(target.trip_id, target.stop_seq)] = max(earlier)[1]
    return result


def _route_delays(db: Session, lines: list[str], now: datetime) -> dict[str, float]:
    if not lines:
        return {}
    rows = db.execute(
        text(
            "SELECT line, sum(mean_delay_seconds * vehicles)::float / nullif(sum(vehicles), 0) "
            "FROM delay_snapshots WHERE observed_at >= :since AND line = ANY(:lines) GROUP BY line"
        ),
        {"since": now - ROUTE_WINDOW, "lines": lines},
    ).all()
    return {line: round(mean, 1) for line, mean in rows if mean is not None}


def _alert_counts(db: Session, route_ids: list[str], now: datetime) -> dict[str, int]:
    if not route_ids:
        return {}
    rows = db.execute(
        text(
            "SELECT e.route_id, count(DISTINCT a.alert_id) FROM alerts a "
            "JOIN alert_entities e ON e.alert_id = a.alert_id "
            "WHERE a.last_seen >= :since AND e.route_id = ANY(:routes) "
            "AND (a.active_from IS NULL OR a.active_from <= :now) "
            "AND (a.active_to IS NULL OR a.active_to >= :now) GROUP BY e.route_id"
        ),
        {"since": now - timedelta(minutes=5), "routes": route_ids, "now": now},
    ).all()
    return dict(rows)


TRAINING_SQL = text(
    """
    SELECT service_date, trip_id, line, stop_id, stop_seq,
           scheduled_at, actual_at, delay_s, gap_s,
           EXTRACT(EPOCH FROM scheduled_at - LAG(scheduled_at, 1) OVER trip_order)::int
               AS scheduled_gap_s
    FROM stop_arrival_events
    WHERE service_date BETWEEN :start AND :end
    WINDOW trip_order AS (PARTITION BY service_date, trip_id ORDER BY stop_seq)
    ORDER BY service_date, trip_id, stop_seq
    """
)


def export_training_rows(engine: Engine, start: date, end: date) -> Iterator[dict]:
    """Recorded arrivals with features from earlier stops of the same trip only.

    An earlier event is a feature only if it was known before the target arrival happened. An
    event becomes known with the observation after the crossing, at the latest ``actual_at +
    gap_s``; two stops crossed between the same pair of observations are interpolated from the
    same later fix, so neither may be a feature of the other. ``features_known_at`` is when the
    newest feature was known: the earliest moment such a prediction could have been made.
    """
    trip: tuple[date, str] | None = None
    earlier: list[tuple[datetime, int]] = []  # (known at, delay) of the trip's previous events
    with engine.connect() as connection:
        result = connection.execution_options(stream_results=True).execute(
            TRAINING_SQL, {"start": start, "end": end}
        )
        for row in result:
            event = row._mapping
            if (event["service_date"], event["trip_id"]) != trip:
                trip, earlier = (event["service_date"], event["trip_id"]), []
            usable = [item for item in earlier if item[0] <= event["actual_at"]]
            local = event["scheduled_at"].astimezone(TIMEZONE)
            yield {
                "service_date": event["service_date"],
                "trip_id": event["trip_id"],
                "line": event["line"],
                "stop_id": event["stop_id"],
                "stop_seq": event["stop_seq"],
                "scheduled_at": event["scheduled_at"],
                "actual_at": event["actual_at"],
                "target_delay_s": event["delay_s"],
                "previous_stop_delay_s": usable[-1][1] if usable else None,
                "delay_two_stops_back_s": usable[-2][1] if len(usable) > 1 else None,
                "features_known_at": usable[-1][0] if usable else None,
                "scheduled_gap_s": event["scheduled_gap_s"],
                "gap_s": event["gap_s"],
                "hour_local": local.hour,
                "weekday": local.weekday(),
            }
            known_at = event["actual_at"] + timedelta(seconds=event["gap_s"])
            earlier.append((known_at, event["delay_s"]))


def write_jsonl(rows: Iterable[dict], handle) -> int:
    count = 0
    for row in rows:
        handle.write(json.dumps(row, default=_json_default, ensure_ascii=False) + "\n")
        count += 1
    return count


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value))


def main() -> None:
    import argparse
    import sys

    from .database import engine

    parser = argparse.ArgumentParser(prog="python -m app.features", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="write training rows as JSON lines")
    export.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    export.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    export.add_argument("--out", default="-", help="file path, or - for standard output")
    args = parser.parse_args()
    rows = export_training_rows(engine, args.start, args.end)
    if args.out == "-":
        count = write_jsonl(rows, sys.stdout)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            count = write_jsonl(rows, handle)
    print(f"{count} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
