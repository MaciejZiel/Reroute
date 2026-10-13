"""Database access of the collector: timetable lookups and batched writes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from gtfs_rt_kit import Alert, TranslatedString
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import Route, Stop, StopPattern, TripSchedule
from ..timeutil import trip_service_date
from . import partitions, tables
from .derive import MatchStatus, PollResult, TripTimetable

LATEST_COLUMNS = [column.name for column in tables.vehicle_latest.columns]
OBSERVATION_COLUMNS = [column.name for column in tables.vehicle_observations.columns]
ARRIVAL_COLUMNS = [column.name for column in tables.stop_arrival_events.columns]
BATCH = 2000
# Written by the live loop on every tick, also when the upstream feed fails: the API reads it to
# know a collector is running, so it never polls the upstream feed itself in that case.
HEARTBEAT_KEY = "collector:heartbeat"


class TimetableCache:
    """Trip timetables from the database, kept in memory because trips repeat every poll."""

    def __init__(self, engine: Engine, max_trips: int = 40_000):
        self.engine = engine
        self.max_trips = max_trips
        self._trips: dict[str, TripTimetable | None] = {}
        self._stops: dict[str, tuple[float, float]] = {}
        self._routes: dict[str, tuple[str, str]] = {}
        self._routes_by_name: dict[str, tuple[str, str, str]] = {}
        self.version: object = None

    def refresh_static(self, session: Session, version: object) -> None:
        """Reload stops and routes (and forget trips) when a new timetable was imported."""
        if version == self.version and self._stops:
            return
        self._stops = {
            stop_id: (latitude, longitude)
            for stop_id, latitude, longitude in session.execute(
                select(Stop.id, Stop.latitude, Stop.longitude).where(Stop.is_demo.is_(False))
            )
        }
        self._routes = {}
        self._routes_by_name = {}
        for route_id, short_name, mode in session.execute(
            select(Route.id, Route.short_name, Route.mode)
            .where(Route.is_demo.is_(False))
            .order_by(Route.id)
        ):
            self._routes[route_id] = (short_name, mode)
            self._routes_by_name.setdefault(short_name.casefold(), (route_id, short_name, mode))
        self._trips = {}
        self.version = version

    def route_for_name(self, name: str) -> tuple[str, str, str] | None:
        """(route id, short name, mode) of a line number, if it is in the timetable."""
        return self._routes_by_name.get(name.casefold())

    def __call__(self, trip_ids: Iterable[str]) -> dict[str, TripTimetable]:
        requested = list(trip_ids)
        wanted = [trip_id for trip_id in requested if trip_id not in self._trips]
        if wanted:
            if len(self._trips) > self.max_trips:
                self._trips.clear()
            self._load(sorted(wanted))
        return {t: found for t in requested if (found := self._trips.get(t)) is not None}

    def _load(self, wanted: list[str]) -> None:
        with Session(self.engine) as session:
            rows = []
            for start in range(0, len(wanted), 1000):
                rows.extend(
                    session.execute(
                        select(
                            TripSchedule.trip_id,
                            TripSchedule.route_id,
                            TripSchedule.service_date,
                            TripSchedule.departures,
                            StopPattern.stop_ids,
                        )
                        .join(StopPattern, StopPattern.id == TripSchedule.pattern_id)
                        .where(TripSchedule.trip_id.in_(wanted[start : start + 1000]))
                    ).all()
                )
        found = set()
        for trip_id, route_id, service_date, departures, pattern in rows:
            found.add(trip_id)
            stop_ids = pattern.split(",")
            times = [int(value) for value in departures.split(",")]
            kept = [
                (stop_id, *self._stops[stop_id], time)
                for stop_id, time in zip(stop_ids, times, strict=False)
                if stop_id in self._stops
            ]
            line, mode = self._routes.get(route_id, (route_id, "bus"))
            self._trips[trip_id] = TripTimetable(
                trip_id=trip_id,
                route_id=route_id,
                line=line,
                mode=mode,
                service_date=service_date or trip_service_date(trip_id),
                stop_ids=tuple(item[0] for item in kept),
                latitudes=tuple(item[1] for item in kept),
                longitudes=tuple(item[2] for item in kept),
                departures=tuple(item[3] for item in kept),
            )
        for trip_id in wanted:
            if trip_id not in found:
                self._trips[trip_id] = None  # remembered, so unknown trips are not re-queried


def write_poll(engine: Engine, result: PollResult, store_unmatched: bool = True) -> None:
    """Write one poll in a single transaction."""
    rows = [
        {name: row[name] for name in OBSERVATION_COLUMNS}
        for row in result.observations
        if store_unmatched or row["match_status"] != int(MatchStatus.NO_TRIP)
    ]
    arrivals = [{name: event[name] for name in ARRIVAL_COLUMNS} for event in result.arrivals]
    with engine.begin() as connection:
        partitions.ensure_observation_partitions(
            connection, (partitions.utc_day(row["observed_at"]) for row in rows)
        )
        partitions.ensure_arrival_partitions(
            connection, (event["service_date"] for event in arrivals)
        )
        for start in range(0, len(rows), BATCH):
            connection.execute(
                insert(tables.vehicle_observations).on_conflict_do_nothing(),
                rows[start : start + BATCH],
            )
        for start in range(0, len(arrivals), BATCH):
            connection.execute(
                insert(tables.stop_arrival_events).on_conflict_do_nothing(),
                arrivals[start : start + BATCH],
            )


def upsert_latest(engine: Engine, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    latest = [{name: row.get(name) for name in LATEST_COLUMNS} for row in rows]
    statement = insert(tables.vehicle_latest)
    update = {name: statement.excluded[name] for name in LATEST_COLUMNS if name != "vehicle_id"}
    with engine.begin() as connection:
        for start in range(0, len(latest), BATCH):
            connection.execute(
                statement.on_conflict_do_update(index_elements=["vehicle_id"], set_=update),
                latest[start : start + BATCH],
            )


def prune_latest(engine: Engine, now: datetime, max_age: timedelta = timedelta(minutes=5)) -> None:
    with engine.begin() as connection:
        connection.execute(
            delete(tables.vehicle_latest).where(tables.vehicle_latest.c.observed_at < now - max_age)
        )


def _text(value: TranslatedString | None, language: str | None = None) -> str | None:
    return value.text(language) if value else None


def upsert_alerts(engine: Engine, alerts: Iterable[Alert], now: datetime) -> int:
    """Store the alerts of the feed and refresh ``last_seen``; returns how many there were."""
    count = 0
    with engine.begin() as connection:
        for alert in alerts:
            count += 1
            starts = [p.start for p in alert.active_periods if p.start]
            ends = [p.end for p in alert.active_periods if p.end]
            values = {
                "alert_id": alert.entity_id,
                "last_seen": now,
                "active_from": min(starts) if starts else None,
                "active_to": max(ends) if ends and len(ends) == len(alert.active_periods) else None,
                "cause": alert.cause,
                "effect": alert.effect,
                "header_pl": alert.header_text.text("pl") if alert.header_text else None,
                "header_en": alert.header_text.text("en") if alert.header_text else None,
                "description_pl": alert.description_text.text("pl")
                if alert.description_text
                else None,
                "description_en": alert.description_text.text("en")
                if alert.description_text
                else None,
                "url": alert.url.text() if alert.url else None,
            }
            statement = insert(tables.alerts).values(first_seen=now, **values)
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["alert_id"],
                    set_={k: statement.excluded[k] for k in values if k != "alert_id"},
                )
            )
            connection.execute(
                delete(tables.alert_entities).where(
                    tables.alert_entities.c.alert_id == alert.entity_id
                )
            )
            entities = [
                {
                    "alert_id": alert.entity_id,
                    "route_id": selector.route_id
                    or (selector.trip.route_id if selector.trip else None),
                    "stop_id": selector.stop_id,
                    "trip_id": selector.trip.trip_id if selector.trip else None,
                }
                for selector in alert.informed_entities
            ]
            if entities:
                connection.execute(insert(tables.alert_entities), entities)
    return count


def get_state(engine: Engine, key: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            select(tables.collector_state.c.value).where(tables.collector_state.c.key == key)
        ).scalar_one_or_none()


def set_state(engine: Engine, key: str, value: str) -> None:
    statement = insert(tables.collector_state).values(
        key=key, value=value, updated_at=datetime.now(UTC)
    )
    with engine.begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "value": statement.excluded.value,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )


def housekeeping(engine: Engine, now: datetime, obs_days: int, arrival_days: int) -> list[str]:
    """Create the partitions for the next days and drop the expired ones."""
    with engine.begin() as connection:
        today = partitions.utc_day(now)
        partitions.ensure_observation_partitions(
            connection, [today + timedelta(days=offset) for offset in range(0, 3)]
        )
        partitions.ensure_arrival_partitions(
            connection, [today - timedelta(days=1), today + timedelta(days=2)]
        )
        return partitions.drop_expired(connection, now, obs_days, arrival_days)
