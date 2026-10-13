"""Create and drop the time partitions of the observation and arrival tables."""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, text

OBSERVATIONS = "vehicle_observations"
ARRIVALS = "stop_arrival_events"
_DAILY = re.compile(r"^vehicle_observations_p(\d{8})$")
_MONTHLY = re.compile(r"^stop_arrival_events_p(\d{6})$")


def utc_day(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def ensure_observation_partitions(connection: Connection, days: Iterable[date]) -> None:
    for day in sorted(set(days)):
        name = f"{OBSERVATIONS}_p{day:%Y%m%d}"
        end = day + timedelta(days=1)
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {OBSERVATIONS} "
                f"FOR VALUES FROM ('{day} 00:00:00+00') TO ('{end} 00:00:00+00')"
            )
        )


def ensure_arrival_partitions(connection: Connection, days: Iterable[date]) -> None:
    for first in sorted({day.replace(day=1) for day in days}):
        following = (first + timedelta(days=32)).replace(day=1)
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {ARRIVALS}_p{first:%Y%m} PARTITION OF {ARRIVALS} "
                f"FOR VALUES FROM ('{first}') TO ('{following}')"
            )
        )


def _partitions(connection: Connection, parent: str) -> list[str]:
    return list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = :parent"
            ),
            {"parent": parent},
        ).scalars()
    )


def drop_expired(
    connection: Connection,
    now: datetime,
    observation_days: int,
    arrival_days: int,
) -> list[str]:
    """Drop whole partitions that lie completely before the retention horizon."""
    dropped = []
    horizon = utc_day(now) - timedelta(days=observation_days)
    for name in _partitions(connection, OBSERVATIONS):
        match = _DAILY.match(name)
        if match and datetime.strptime(match.group(1), "%Y%m%d").date() < horizon:
            connection.execute(text(f"DROP TABLE {name}"))
            dropped.append(name)
    arrival_horizon = utc_day(now) - timedelta(days=arrival_days)
    for name in _partitions(connection, ARRIVALS):
        match = _MONTHLY.match(name)
        if not match:
            continue
        first = datetime.strptime(match.group(1), "%Y%m").date()
        last_day = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if last_day < arrival_horizon:
            connection.execute(text(f"DROP TABLE {name}"))
            dropped.append(name)
    return dropped
