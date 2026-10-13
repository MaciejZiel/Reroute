"""Feature building and training export for the delay prediction seam."""

import io
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.collector import store
from app.collector.derive import PollResult
from app.features import (
    Target,
    export_training_rows,
    live_feature_rows,
    slope_per_minute,
    write_jsonl,
)

DAY = date(2026, 10, 13)


def test_slope_is_the_least_squares_trend_in_seconds_per_minute() -> None:
    start = datetime(2026, 10, 13, 12, tzinfo=UTC)
    rising = [(start + timedelta(minutes=m), 60 * m) for m in range(5)]
    flat = [(start + timedelta(minutes=m), 30) for m in range(5)]

    assert slope_per_minute(rising) == pytest.approx(60)
    assert slope_per_minute(flat) == pytest.approx(0)
    assert slope_per_minute(rising[:2]) is None  # too few points to call it a trend


def _event(seq: int, delay: int, minute: int) -> dict:
    scheduled = datetime(2026, 10, 13, 10, minute, tzinfo=UTC)
    return {
        "service_date": DAY,
        "trip_id": "T1",
        "stop_seq": seq,
        "stop_id": f"s{seq}",
        "line": "9",
        "scheduled_at": scheduled,
        "actual_at": scheduled + timedelta(seconds=delay),
        "delay_s": delay,
        "gap_s": 30,
    }


def test_training_rows_only_use_earlier_stops_of_the_same_trip(
    db: Session, pg_engine: Engine
) -> None:
    events = [_event(2, 30, 0), _event(3, 60, 5), _event(4, 90, 10)]
    other = _event(2, 500, 1) | {"trip_id": "T2"}
    store.write_poll(pg_engine, PollResult(arrivals=[*events, other]))

    rows = list(export_training_rows(pg_engine, DAY, DAY))

    t1 = [row for row in rows if row["trip_id"] == "T1"]
    assert [row["target_delay_s"] for row in t1] == [30, 60, 90]
    assert [row["previous_stop_delay_s"] for row in t1] == [None, 30, 60]
    assert [row["delay_two_stops_back_s"] for row in t1] == [None, None, 30]
    assert [row["scheduled_gap_s"] for row in t1] == [None, 300, 300]
    # The other trip's delay never leaks into this trip's features.
    assert next(row for row in rows if row["trip_id"] == "T2")["previous_stop_delay_s"] is None
    assert t1[0]["hour_local"] == 12 and t1[0]["weekday"] == 1  # 10:00 UTC is 12:00 CEST, Tuesday

    out = io.StringIO()
    assert write_jsonl(iter(rows), out) == 4
    assert json.loads(out.getvalue().splitlines()[0])["trip_id"] == "T1"


def test_live_features_know_the_vehicle_delay_and_the_previous_stop(
    db: Session, pg_engine: Engine
) -> None:
    store.write_poll(pg_engine, PollResult(arrivals=[_event(2, 45, 0)]))
    now = datetime(2026, 10, 13, 10, 3, tzinfo=UTC)
    target = Target(
        trip_id="T1",
        line="9",
        route_id="9",
        stop_id="s4",
        stop_seq=4,
        scheduled_at=datetime(2026, 10, 13, 10, 10, tzinfo=UTC),
        service_date=DAY,
        vehicle_id="V/9/1",
        current_delay_s=50,
        next_stop_seq=3,
    )

    (row,) = live_feature_rows(db, [target], now)

    assert row.current_delay_s == 50
    assert row.previous_stop_delay_s == 45
    assert row.stops_remaining == 1  # one stop (3) lies between the vehicle and stop 4
    assert (row.hour_local, row.weekday) == (12, 1)
    assert row.active_alerts == 0
