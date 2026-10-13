"""Service-day arithmetic: after-midnight GTFS times and DST changes."""

from datetime import UTC, date, datetime

from app.timeutil import (
    TIMEZONE,
    service_dates_around,
    service_instant,
    service_seconds,
    trip_service_date,
)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TIMEZONE).astimezone(UTC)


def test_ordinary_day_matches_local_wall_clock() -> None:
    assert service_instant(date(2026, 10, 13), 8 * 3600 + 15 * 60) == _local(2026, 10, 13, 8, 15)


def test_times_past_midnight_belong_to_the_next_calendar_day() -> None:
    # 25:10:00 on the service day of 13 October is 01:10 on 14 October.
    assert service_instant(date(2026, 10, 13), 25 * 3600 + 600) == _local(2026, 10, 14, 1, 10)


def test_spring_forward_day_has_23_hours() -> None:
    # 29 March 2026: 02:00 CET jumps to 03:00 CEST. GTFS counts from noon minus 12 h, so the
    # origin is 23:00 on the previous local day, and elapsed time includes the lost hour.
    assert service_instant(date(2026, 3, 29), 0) == _local(2026, 3, 28, 23)
    assert service_instant(date(2026, 3, 29), 7 * 3600) == _local(2026, 3, 29, 7)
    assert service_instant(date(2026, 3, 29), 5 * 3600) == _local(2026, 3, 29, 5)


def test_autumn_day_has_25_hours() -> None:
    # 25 October 2026: 03:00 CEST falls back to 02:00 CET. The origin is 01:00 CEST.
    assert service_instant(date(2026, 10, 25), 0) == _local(2026, 10, 25, 1)
    assert service_instant(date(2026, 10, 25), 6 * 3600) == _local(2026, 10, 25, 6)
    assert service_instant(date(2026, 10, 25), 12 * 3600) == _local(2026, 10, 25, 12)


def test_service_seconds_inverts_service_instant_across_dst() -> None:
    for day in (date(2026, 3, 29), date(2026, 10, 25), date(2026, 7, 1)):
        for seconds in (0, 3600, 12 * 3600, 26 * 3600 + 5):
            assert service_seconds(day, service_instant(day, seconds)) == seconds


def test_service_dates_around_uses_the_warsaw_date() -> None:
    # 22:30 UTC on 12 October is already 00:30 on 13 October in Warsaw.
    instant = datetime(2026, 10, 12, 22, 30, tzinfo=UTC)
    assert service_dates_around(instant) == (date(2026, 10, 12), date(2026, 10, 13))


def test_trip_service_date_reads_the_trip_id_prefix() -> None:
    assert trip_service_date("2026-10-13:140:PcS:2:1543") == date(2026, 10, 13)
    assert trip_service_date("b1") is None
    assert trip_service_date("2026-13-45:x") is None
