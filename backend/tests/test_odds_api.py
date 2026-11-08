"""The "chance of a delay" view: nightly counts of recorded arrivals per line, platform, kind of
day and scheduled hour, served per station."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_live_api import client, network  # noqa: F401

from app import odds
from app.collector import store
from app.collector.derive import PollResult

WARSAW = ZoneInfo("Europe/Warsaw")


def _arrivals(local_day: datetime, delays: list[int]) -> list[dict]:
    """Arrivals of line 9 at platform 100101, one trip each, scheduled at 08:10 Warsaw time."""
    scheduled = local_day.replace(hour=8, minute=10).astimezone(UTC)
    return [
        {
            "service_date": local_day.date(),
            "trip_id": f"odds-{local_day.date()}-{i}",
            "stop_seq": 2,
            "stop_id": "100101",
            "line": "9",
            "scheduled_at": scheduled,
            "actual_at": scheduled + timedelta(seconds=delay),
            "delay_s": delay,
            "gap_s": 20,
        }
        for i, delay in enumerate(delays)
    ]


def test_odds_count_late_shares_per_day_type_and_hour(
    client: TestClient,  # noqa: F811
    network: Session,  # noqa: F811
    pg_engine: Engine,
) -> None:
    today = datetime.now(WARSAW).replace(hour=0, minute=0, second=0, microsecond=0)
    # The last weekday before today, and a Saturday, both inside the 28-day window.
    weekday = next(
        today - timedelta(days=d)
        for d in range(1, 8)
        if (today - timedelta(days=d)).isoweekday() <= 5
    )
    saturday = next(
        today - timedelta(days=d)
        for d in range(1, 8)
        if (today - timedelta(days=d)).isoweekday() == 6
    )
    rows = _arrivals(weekday, [0, 60, 200, 400, -90]) + _arrivals(saturday, [30, 30])
    store.write_poll(pg_engine, PollResult(arrivals=rows))
    old = _arrivals(today - timedelta(days=40), [900])  # outside the window
    store.write_poll(pg_engine, PollResult(arrivals=old))

    odds.refresh(pg_engine)
    odds.refresh(pg_engine)  # the second run is the concurrent one
    body = client.get("/api/stops/100101/odds").json()

    assert body["ready"] is True and body["window_days"] == 28
    line = next(
        item for item in body["lines"] if item["line"] == "9" and item["stop_id"] == "100101"
    )
    assert line["platform"] == "01" and line["mode"] == "tram"
    cells = {(cell["day_type"], cell["hour"]): cell for cell in line["cells"]}
    work = cells[("weekday", 8)]
    assert work["n"] == 5 and work["days"] == 1
    assert work["late3_share"] == 0.4 and work["late5_share"] == 0.2 and work["early_share"] == 0.2
    assert work["median_s"] == 60
    assert cells[("saturday", 8)]["n"] == 2 and cells[("saturday", 8)]["late3_share"] == 0.0
    assert all(cell["n"] < 10 for cell in line["cells"])  # the 40-day-old arrival is not counted
    assert client.get("/api/stops/nope/odds").status_code == 404


def test_the_count_is_due_once_a_warsaw_night_whatever_happened_in_between() -> None:
    night = datetime(2026, 11, 8, 2, 30, tzinfo=WARSAW)
    morning = datetime(2026, 11, 8, 3, 30, tzinfo=WARSAW)
    last_night = datetime(2026, 11, 7, 3, 5, tzinfo=WARSAW)
    assert odds.due(night, None, populated=True)  # never counted
    assert odds.due(night, last_night, populated=False)  # empty view
    assert not odds.due(night, last_night, populated=True)  # wait for 03:00
    assert odds.due(morning, last_night, populated=True)
    assert not odds.due(morning + timedelta(hours=5), morning, populated=True)
    # back after three days down, at 10:00: the old count is due at once
    assert odds.due(morning + timedelta(days=3, hours=7), last_night, populated=True)


def test_a_refresh_is_recorded_and_not_repeated_the_same_night(pg_engine: Engine) -> None:
    refresher = odds.OddsRefresher(pg_engine)
    store.set_state(pg_engine, odds.STATE_KEY, "2000-01-01T00:00:00+00:00")
    assert refresher.tick(datetime.now(UTC))
    refresher.thread.join(timeout=60)
    assert refresher.refreshed_at() > datetime.now(UTC) - timedelta(minutes=1)
    assert not refresher.tick(datetime.now(UTC))
