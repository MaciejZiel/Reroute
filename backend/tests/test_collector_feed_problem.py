from datetime import UTC, datetime, timedelta

from gtfs_rt_kit import Feed, FeedHeader, VehiclePosition

from app.collector.service import STALE_FEED_AGE_S, feed_problem

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def feed(age_s: float | None, vehicles: int = 1) -> Feed:
    stamp = None if age_s is None else NOW - timedelta(seconds=age_s)
    return Feed(
        header=FeedHeader(gtfs_realtime_version="2.0", timestamp=stamp),
        vehicles=tuple(VehiclePosition(entity_id=str(i)) for i in range(vehicles)),
    )


def test_a_fresh_feed_with_vehicles_is_fine() -> None:
    assert feed_problem(feed(30), NOW) is None
    assert feed_problem(feed(None), NOW) is None  # no header time: nothing to judge by


def test_an_empty_feed_is_reported() -> None:
    assert feed_problem(feed(30, vehicles=0), NOW) == "the feed carries no vehicles"


def test_a_frozen_feed_is_reported_with_its_age() -> None:
    assert feed_problem(feed(STALE_FEED_AGE_S - 1), NOW) is None
    assert feed_problem(feed(3 * 3600), NOW) == "the feed snapshot is 180 min old"
