"""Service-day arithmetic for GTFS times in Warsaw local time."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Warsaw")
_TRIP_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}):")


def _origin(service_date: date) -> datetime:
    # Subtract in UTC: aware arithmetic in a zone is wall-clock arithmetic and would ignore a
    # DST change between noon and midnight.
    noon = datetime.combine(service_date, time(12), TIMEZONE).astimezone(UTC)
    return noon - timedelta(hours=12)


def service_instant(service_date: date, seconds: int) -> datetime:
    """UTC instant of a GTFS time: seconds elapsed since noon minus 12 h of the service day.

    GTFS counts from "noon minus 12 hours", not midnight, so times above 24:00:00 and the
    23 h / 25 h days of the DST changes come out right (a 02:30 departure on the spring-forward
    night does not exist locally, but its instant is still well defined).
    """
    return _origin(service_date) + timedelta(seconds=seconds)


def service_seconds(service_date: date, instant: datetime) -> float:
    """Inverse of :func:`service_instant`: seconds of ``instant`` on that service day."""
    return (instant - _origin(service_date)).total_seconds()


def service_dates_around(instant: datetime) -> tuple[date, date]:
    """Yesterday's and today's Warsaw dates: the service days that can still be running."""
    today = instant.astimezone(TIMEZONE).date()
    return today - timedelta(days=1), today


def trip_service_date(trip_id: str) -> date | None:
    """Service date embedded in Warsaw trip ids such as ``2026-10-13:140:PcS:2:1543``."""
    match = _TRIP_DATE.match(trip_id)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
