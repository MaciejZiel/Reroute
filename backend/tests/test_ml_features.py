"""Feature definitions: nothing known only after the prediction moment may reach a feature."""

import math
from dataclasses import replace

import pytest

from app.ml.explain import explain
from app.ml.featurize import (
    FEATURE_NAMES,
    Event,
    EventIndex,
    TargetStop,
    feature_vector,
    known_trip_events,
    trend,
)

BASE = 1_760_000_000.0  # 2025-10-09, any instant will do


def ev(trip: str, seq: int, delay: int, minute: float, line: str = "9", gap: int = 30) -> Event:
    scheduled = BASE + minute * 60
    actual = scheduled + delay
    return Event(trip, seq, f"s{seq}", line, scheduled, actual, delay, actual + gap)


def trips() -> list[Event]:
    events = []
    for number, (trip, offset) in enumerate([("A", 0), ("B", 10), ("C", 20)]):
        for seq in range(1, 7):
            events.append(ev(trip, seq, 20 * seq + 30 * number, offset + 3 * seq))
    return events


def features(events: list[Event], trip: str, origin_seq: int, target_seq: int) -> dict:
    index = EventIndex(events)
    origin = next(e for e in index.by_trip[trip] if e.stop_seq == origin_seq)
    target = next(e for e in index.by_trip[trip] if e.stop_seq == target_seq)
    spec = TargetStop(trip, target_seq, target.stop_id, target.line, target.scheduled_s)
    t0 = origin.known_s
    vector = feature_vector(index, spec, origin, t0, known_trip_events(index, trip, t0))
    return dict(zip(FEATURE_NAMES, vector, strict=True))


def test_features_describe_the_origin_and_the_distance_to_the_target() -> None:
    got = features(trips(), "B", 2, 5)
    assert got["stops_ahead"] == 3
    assert got["origin_delay_s"] == 40 + 30
    assert math.isnan(got["vehicle_delay_s"])
    assert got["prev_delay_change_s"] == 20
    assert got["sched_run_s"] == 9 * 60
    assert got["is_tram"] == 1  # line "9" is numbered below 100


def test_the_vehicle_ahead_and_the_stop_history_are_the_other_trips_only() -> None:
    got = features(trips(), "B", 2, 5)
    # trip A reached stop 5 at minute 15 (+100 s), long before B's origin became known (minute 16)
    assert got["leader_delay_s"] == 100
    assert got["headway_s"] == 10 * 60
    assert got["stop_recent_delay_s"] == 100  # only A has been at stop 5
    assert not math.isnan(got["line_recent_delay_s"])


def test_changing_anything_that_happens_after_the_prediction_moment_changes_no_feature() -> None:
    events = trips()
    before = features(events, "B", 2, 5)
    origin_known = next(e for e in events if e.trip_id == "B" and e.stop_seq == 2).known_s
    tampered = [
        replace(e, delay_s=e.delay_s + 999, actual_s=e.actual_s + 999, known_s=e.known_s + 999)
        if e.known_s > origin_known and not (e.trip_id == "B" and e.stop_seq <= 2)
        else e
        for e in events
    ]
    # The target itself (B, stop 5) is in the tampered set too: its own delay is the label.
    after = features(tampered, "B", 2, 5)
    for name in FEATURE_NAMES:
        assert before[name] == after[name] or (math.isnan(before[name]) and math.isnan(after[name]))


def test_a_trip_never_counts_as_its_own_line_history() -> None:
    events = [ev("A", seq, 100, seq) for seq in range(1, 6)]
    got = features(events, "A", 3, 5)
    assert math.isnan(got["line_recent_delay_s"])
    assert math.isnan(got["leader_delay_s"])


def test_trend_is_the_least_squares_slope_per_minute() -> None:
    assert trend([(0, 0), (60, 60), (120, 120)]) == pytest.approx(60)
    assert math.isnan(trend([(0, 0), (60, 60)]))


def test_explanations_are_sorted_by_effect_and_ignore_noise() -> None:
    names = ["leader_delay_s", "stops_ahead", "origin_delay_s", "is_tram"]
    why = explain(names, [300.0, 3.0, 120.0, 1.0], [40.0, -12.0, 2.0, 1.0], 120.0, 160.0)
    assert [f.feature for f in why.factors] == ["leader_delay_s", "stops_ahead"]
    assert "5 min late" in why.factors[0].text
    assert why.summary.startswith("Now 2 min late; expected 2 min 40 s late at this stop. Main")
    assert "(+40 s)" in why.summary and "(-12 s)" in why.summary
    quiet = explain(names, [0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], 0.0, 0.0)
    assert quiet.factors == () and "no strong signal" in quiet.summary


def test_stops_ahead_and_running_time_are_one_distance_factor_and_seconds_are_exact() -> None:
    names = ["stops_ahead", "sched_run_s", "leader_delay_s"]
    why = explain(names, [3.0, 180.0, 120.0], [-23.0, 15.0, 4.0], 0.0, 244.0)
    assert [f.feature for f in why.factors] == ["distance"]
    assert why.factors[0].effect_s == -8
    assert "3 stops ahead, 3 min of running time" in why.factors[0].text
    assert "expected 4 min 4 s late" in why.summary  # 244 s is not rounded down to 4 min
