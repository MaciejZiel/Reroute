"""Upcoming departures at a station, from the timetable, with live delays where matched."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Integer, and_, func, literal, or_, select
from sqlalchemy.orm import Session

from .collector import tables
from .features import Target, live_feature_rows
from .live import LiveVehicle, vehicles_of_trips
from .models import PatternStop, Stop, StopPattern, TripSchedule
from .predict import Prediction, get_predictor
from .stops import StopInfo
from .timeutil import service_dates_around, service_instant, service_seconds, trip_service_date

DEFAULT_HORIZON = timedelta(hours=2)
GRACE = timedelta(seconds=60)


@dataclass
class Candidate:
    """One trip's call at one platform."""

    trip_id: str
    route_id: str
    service_date: date
    headsign: str
    stop_id: str
    position: int  # 0-based index of the stop in the trip
    scheduled_at: datetime
    vehicle: LiveVehicle | None = None
    prediction: Prediction | None = None

    @property
    def expected_at(self) -> datetime:
        if self.prediction is not None and self.prediction.basis == "live_delay":
            return self.scheduled_at + timedelta(seconds=self.prediction.predicted_delay_s)
        return self.scheduled_at


def _stop_count():
    return (
        func.length(StopPattern.stop_ids)
        - func.length(func.replace(StopPattern.stop_ids, ",", ""))
        + 1
    )


def scheduled_candidates(
    db: Session, stop_ids: list[str], now: datetime, horizon: timedelta
) -> list[Candidate]:
    """Trips calling at the stops between now and the horizon, straight from the timetable."""
    dates = _service_dates(now, horizon)
    departure = func.split_part(TripSchedule.departures, literal(","), PatternStop.position + 1)
    inner = (
        select(
            TripSchedule.trip_id,
            TripSchedule.route_id,
            TripSchedule.service_date,
            TripSchedule.headsign,
            PatternStop.stop_id,
            PatternStop.position,
            departure.cast(Integer).label("departure_s"),
        )
        .join(PatternStop, PatternStop.pattern_id == TripSchedule.pattern_id)
        .join(StopPattern, StopPattern.id == TripSchedule.pattern_id)
        .where(
            PatternStop.stop_id.in_(stop_ids),
            TripSchedule.service_date.in_(dates),
            PatternStop.position < _stop_count() - 1,
        )
        .subquery()
    )
    windows = or_(
        *(
            and_(
                inner.c.service_date == day,
                inner.c.departure_s.between(
                    int(service_seconds(day, now - GRACE)), int(service_seconds(day, now + horizon))
                ),
            )
            for day in dates
        )
    )
    rows = db.execute(select(inner).where(windows)).all()
    return [
        Candidate(
            trip_id=row.trip_id,
            route_id=row.route_id,
            service_date=row.service_date,
            headsign=row.headsign,
            stop_id=row.stop_id,
            position=row.position,
            scheduled_at=service_instant(row.service_date, row.departure_s),
        )
        for row in rows
    ]


def live_candidates(db: Session, stop_ids: list[str], now: datetime) -> list[Candidate]:
    """Trips whose vehicle has not yet passed the stops, however late they are running."""
    latest = tables.vehicle_latest
    departure = func.split_part(TripSchedule.departures, literal(","), PatternStop.position + 1)
    rows = db.execute(
        select(
            TripSchedule.trip_id,
            TripSchedule.route_id,
            TripSchedule.service_date,
            TripSchedule.headsign,
            PatternStop.stop_id,
            PatternStop.position,
            departure.cast(Integer).label("departure_s"),
        )
        .select_from(latest)
        .join(TripSchedule, TripSchedule.trip_id == latest.c.trip_id)
        .join(PatternStop, PatternStop.pattern_id == TripSchedule.pattern_id)
        .join(StopPattern, StopPattern.id == TripSchedule.pattern_id)
        .where(
            PatternStop.stop_id.in_(stop_ids),
            latest.c.observed_at > now - timedelta(minutes=3),
            latest.c.next_stop_seq.is_not(None),
            PatternStop.position >= latest.c.next_stop_seq - 1,
            PatternStop.position < _stop_count() - 1,
            TripSchedule.service_date.is_not(None),
        )
    ).all()
    return [
        Candidate(
            trip_id=row.trip_id,
            route_id=row.route_id,
            service_date=row.service_date,
            headsign=row.headsign,
            stop_id=row.stop_id,
            position=row.position,
            scheduled_at=service_instant(row.service_date, row.departure_s),
        )
        for row in rows
    ]


def _service_dates(now: datetime, horizon: timedelta) -> list[date]:
    yesterday, today = service_dates_around(now)
    end = service_dates_around(now + horizon)[1]
    return sorted({yesterday, today, end})


def upcoming(
    db: Session,
    stops: list[StopInfo],
    now: datetime,
    horizon: timedelta = DEFAULT_HORIZON,
    line_names: dict[str, str] | None = None,
    per_line: int | None = None,
    total: int | None = None,
) -> list[Candidate]:
    """Departures within the horizon (expected time, so a late bus stays until it passes).

    The prediction is the expensive step (history queries, model evaluation), so with ``per_line``
    and/or ``total`` only the candidates that can still make the cut are predicted: they are
    ranked by the delay their vehicle reports now, and a few more than asked for are kept
    (``TRIM_SLACK``) because the prediction can move a departure by a minute or two. The caller
    must not ask for more entries per line (or in total) than it passes here.
    """
    stop_ids = [stop.id for stop in stops]
    merged = {(c.trip_id, c.position): c for c in scheduled_candidates(db, stop_ids, now, horizon)}
    for candidate in live_candidates(db, stop_ids, now):
        merged.setdefault((candidate.trip_id, candidate.position), candidate)
    candidates = list(merged.values())
    vehicles = vehicles_of_trips(db, sorted({candidate.trip_id for candidate in candidates}))
    result = []
    for candidate in candidates:
        vehicle = vehicles.get(candidate.trip_id)
        if vehicle is not None:
            if vehicle.next_stop_seq is not None and vehicle.next_stop_seq - 1 > candidate.position:
                continue  # already passed this stop
            candidate.vehicle = vehicle
        result.append(candidate)
    result = _trim(result, now, horizon, per_line, total)
    _predict(db, result, line_names or {}, now)
    return [
        candidate for candidate in result if now - GRACE <= candidate.expected_at <= now + horizon
    ]


TRIM_SLACK = 3
TRIM_MARGIN = timedelta(minutes=10)


def _reported_expected_at(candidate: Candidate) -> datetime:
    vehicle = candidate.vehicle
    if vehicle is not None and vehicle.has_delay:
        return candidate.scheduled_at + timedelta(seconds=vehicle.delay_s)
    return candidate.scheduled_at


def _trim(
    candidates: list[Candidate],
    now: datetime,
    horizon: timedelta,
    per_line: int | None,
    total: int | None,
) -> list[Candidate]:
    """Keep the candidates that can still appear in the answer; drop the rest before predicting."""
    if per_line is None and total is None:
        return candidates
    window = [
        c
        for c in candidates
        if now - GRACE - TRIM_MARGIN <= _reported_expected_at(c) <= now + horizon + TRIM_MARGIN
    ]
    window.sort(key=_reported_expected_at)
    if per_line is not None:
        taken: dict[str, int] = defaultdict(int)
        kept = []
        for candidate in window:
            if taken[candidate.route_id] < per_line + TRIM_SLACK:
                taken[candidate.route_id] += 1
                kept.append(candidate)
        window = kept
    if total is not None:
        window = window[: total + TRIM_SLACK]
    return window


def _age(vehicle: LiveVehicle | None, now: datetime) -> float | None:
    return (now - vehicle.observed_at).total_seconds() if vehicle else None


def explain_first(
    db: Session,
    candidates: list[Candidate],
    lines: dict[str, str],
    now: datetime,
    lang: str = "en",
) -> None:
    """Recompute the predictions of exactly these candidates and attach the reasons to the first
    the model answers: the entry a reader sees at the top, not one picked before sorting."""
    _predict(db, candidates, lines, now, explain=True, lang=lang)


def _predict(
    db: Session,
    candidates: list[Candidate],
    lines: dict[str, str],
    now: datetime,
    explain: bool = False,
    lang: str = "en",
) -> None:
    predictor = get_predictor()
    targets = [
        Target(
            trip_id=c.trip_id,
            line=lines.get(c.route_id, c.route_id),
            route_id=c.route_id,
            stop_id=c.stop_id,
            stop_seq=c.position + 1,
            scheduled_at=c.scheduled_at,
            service_date=c.service_date,
            vehicle_id=c.vehicle.vehicle_id if c.vehicle else None,
            current_delay_s=c.vehicle.delay_s if c.vehicle and c.vehicle.has_delay else None,
            next_stop_seq=c.vehicle.next_stop_seq if c.vehicle else None,
            vehicle_age_s=_age(c.vehicle, now),
        )
        for c in candidates
    ]
    rows = live_feature_rows(
        db,
        targets,
        now,
        with_history=predictor.kind != "baseline",
        with_model_inputs=predictor.kind == "model",
    )
    for candidate, prediction in zip(
        candidates, predictor.predict(rows, explain=explain, lang=lang), strict=True
    ):
        candidate.prediction = prediction


def by_route(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda item: item.expected_at):
        grouped[candidate.route_id].append(candidate)
    return grouped


@dataclass(frozen=True)
class TripStop:
    stop_id: str
    name: str
    position: int
    scheduled_at: datetime


@dataclass(frozen=True)
class TripStops:
    trip_id: str
    route_id: str
    service_date: date | None
    headsign: str
    stops: list[TripStop]


def trip_stops(db: Session, trip_id: str) -> TripStops | None:
    """All stops of a scheduled trip with their scheduled instants."""
    row = db.execute(
        select(
            TripSchedule.route_id,
            TripSchedule.service_date,
            TripSchedule.headsign,
            TripSchedule.departures,
            StopPattern.stop_ids,
        )
        .join(StopPattern, StopPattern.id == TripSchedule.pattern_id)
        .where(TripSchedule.trip_id == trip_id)
    ).first()
    if row is None:
        return None
    stop_ids = row.stop_ids.split(",")
    service_date = row.service_date or trip_service_date(trip_id)
    names = dict(db.execute(select(Stop.id, Stop.name).where(Stop.id.in_(set(stop_ids)))).all())
    stops = [
        TripStop(
            stop_id=stop_id,
            name=names.get(stop_id, stop_id),
            position=position,
            scheduled_at=service_instant(service_date, int(departure)),
        )
        for position, (stop_id, departure) in enumerate(
            zip(stop_ids, row.departures.split(","), strict=False)
        )
    ]
    return TripStops(trip_id, row.route_id, service_date, row.headsign, stops)
