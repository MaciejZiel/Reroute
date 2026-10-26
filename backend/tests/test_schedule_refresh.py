"""The collector re-imports a stale timetable in a child process, one at a time."""

from datetime import UTC, datetime, timedelta

from app.collector.config import Settings
from app.collector.schedule_refresh import RETRY_AFTER, ScheduleRefresher

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


class FakeProcess:
    def __init__(self) -> None:
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code


class Spawner:
    def __init__(self) -> None:
        self.started: list[FakeProcess] = []

    def __call__(self) -> FakeProcess:
        process = FakeProcess()
        self.started.append(process)
        return process


def _refresher(downloaded: list[datetime | None], hours: float = 24) -> tuple:
    spawn = Spawner()
    return ScheduleRefresher(hours, lambda: downloaded[0], spawn), spawn


def test_a_fresh_timetable_is_left_alone() -> None:
    refresher, spawn = _refresher([NOW - timedelta(hours=3)])
    assert not refresher.tick(NOW)
    assert spawn.started == []


def test_a_stale_timetable_starts_one_import_and_waits_for_it() -> None:
    downloaded: list[datetime | None] = [NOW - timedelta(days=13)]
    refresher, spawn = _refresher(downloaded)
    assert refresher.tick(NOW)
    assert not refresher.tick(NOW + timedelta(minutes=10))  # still running
    assert len(spawn.started) == 1

    spawn.started[0].code = 0
    downloaded[0] = NOW + timedelta(minutes=15)
    assert not refresher.tick(NOW + timedelta(minutes=20))
    assert len(spawn.started) == 1
    assert refresher.age(NOW + timedelta(minutes=20)) == timedelta(minutes=5)


def test_a_failed_import_is_retried_after_an_hour() -> None:
    refresher, spawn = _refresher([NOW - timedelta(days=2)])
    assert refresher.tick(NOW)
    spawn.started[0].code = 1
    assert not refresher.tick(NOW + timedelta(minutes=10))
    assert not refresher.tick(NOW + timedelta(minutes=10) + RETRY_AFTER - timedelta(seconds=1))
    assert refresher.tick(NOW + timedelta(minutes=10) + RETRY_AFTER)
    assert len(spawn.started) == 2


def test_an_import_that_leaves_the_age_unchanged_does_not_loop() -> None:
    refresher, spawn = _refresher([NOW - timedelta(days=2)])
    assert refresher.tick(NOW)
    spawn.started[0].code = 0  # finished, but the timetable is still the old one
    assert not refresher.tick(NOW + timedelta(minutes=10))
    assert len(spawn.started) == 1


def test_no_timetable_and_a_zero_limit_start_nothing() -> None:
    refresher, spawn = _refresher([None])
    assert not refresher.tick(NOW)
    off, off_spawn = _refresher([NOW - timedelta(days=30)], hours=0)
    assert not off.enabled
    assert not off.tick(NOW)
    assert spawn.started == off_spawn.started == []


def test_a_naive_download_time_counts_as_utc() -> None:
    refresher, _ = _refresher([datetime(2026, 10, 26, 1, 0)])
    assert refresher.age(NOW) == timedelta(hours=2)


def test_a_spawn_error_is_retried_later() -> None:
    def broken() -> FakeProcess:
        raise OSError("no such interpreter")

    refresher = ScheduleRefresher(24, lambda: NOW - timedelta(days=2), broken)
    assert not refresher.tick(NOW)
    assert refresher.not_before == NOW + RETRY_AFTER


def test_the_refresh_interval_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("SCHEDULE_REFRESH_HOURS", raising=False)
    assert Settings.from_env().schedule_refresh_hours == 24.0
    monkeypatch.setenv("SCHEDULE_REFRESH_HOURS", "6")
    assert Settings.from_env().schedule_refresh_hours == 6.0
    monkeypatch.setenv("SCHEDULE_REFRESH_HOURS", "-1")
    assert Settings.from_env().schedule_refresh_hours == 0.0


def test_vehicles_that_stop_matching_trigger_an_early_import() -> None:
    refresher, spawn = _refresher([NOW - timedelta(minutes=30)])
    assert not refresher.tick(NOW, unmatched=True)  # too fresh to blame
    refresher.downloaded_at = lambda: NOW - timedelta(hours=2)
    assert not refresher.tick(NOW)  # matching fine: wait for the daily refresh
    assert refresher.tick(NOW, unmatched=True)
    assert len(spawn.started) == 1
