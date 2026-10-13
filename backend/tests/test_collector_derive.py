"""Observation rows and stop arrival events derived from vehicle positions (pure functions)."""

from datetime import UTC, date, datetime, timedelta

from app.collector.derive import (
    MatchStatus,
    Observation,
    TripTimetable,
    VehicleState,
    derive_poll,
    project,
)
from app.timeutil import service_instant

DAY = date(2026, 10, 13)
LONS = (21.00, 21.01, 21.02, 21.03)


def _timetable(departures=(8 * 3600, 8 * 3600 + 300, 8 * 3600 + 600, 8 * 3600 + 900), day=DAY):
    return TripTimetable(
        trip_id=f"{day}:9:X:1:0800",
        route_id="9",
        line="9",
        mode="tram",
        service_date=day,
        stop_ids=("a", "b", "c", "d"),
        latitudes=(52.23,) * 4,
        longitudes=LONS,
        departures=tuple(departures),
    )


def _obs(timetable, lon, at, vehicle="V/9/1", lat=52.23):
    return Observation(vehicle, "1001", timetable.trip_id, lat, lon, 90.0, None, at)


def _run(timetable, fixes, state=None):
    state = {} if state is None else state
    rows, arrivals = [], []
    for obs in fixes:
        result = derive_poll(
            [obs], lambda ids: {timetable.trip_id: timetable}, state, obs.observed_at
        )
        rows += result.observations
        arrivals += result.arrivals
    return rows, arrivals, state


def _at(day, hour, minute, second=0):
    from zoneinfo import ZoneInfo

    return datetime(
        day.year, day.month, day.day, hour, minute, second, tzinfo=ZoneInfo("Europe/Warsaw")
    ).astimezone(UTC)


def test_on_time_vehicle_has_zero_delay_and_next_stop() -> None:
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.012, _at(DAY, 8, 6))])  # 20 % of leg 1

    row = rows[0]
    assert row["match_status"] == MatchStatus.OK
    assert row["delay_s"] == 0
    assert (row["next_stop_seq"], row["next_stop_id"]) == (3, "c")
    assert row["line"] == "9" and row["mode"] == "tram" and row["service_date"] == DAY


def test_late_vehicle_reports_positive_delay() -> None:
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.015, _at(DAY, 8, 10))])  # due 08:07:30

    assert rows[0]["delay_s"] == 150


def test_vehicle_standing_at_the_first_stop_is_a_layover_not_a_delay() -> None:
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.0, _at(DAY, 7, 50))])

    assert rows[0]["match_status"] == MatchStatus.LAYOVER and rows[0]["delay_s"] is None


def test_vehicle_waiting_just_past_the_first_stop_is_a_layover() -> None:
    # ~35 m past the first stop (5 % of a 680 m leg), ten minutes before departure: still the
    # layover, not a vehicle running ten minutes early.
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.0005, _at(DAY, 7, 50))])

    assert rows[0]["match_status"] == MatchStatus.LAYOVER and rows[0]["delay_s"] is None


def test_far_from_the_route_is_off_route() -> None:
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.012, _at(DAY, 8, 6), lat=52.26)])

    assert rows[0]["match_status"] == MatchStatus.OFF_ROUTE and rows[0]["delay_s"] is None


def test_implausible_delay_is_rejected() -> None:
    timetable = _timetable()
    rows, _, _ = _run(timetable, [_obs(timetable, 21.012, _at(DAY, 11, 0))])  # three hours late

    assert rows[0]["match_status"] == MatchStatus.IMPLAUSIBLE and rows[0]["delay_s"] is None


def test_arrival_times_are_interpolated_between_observations() -> None:
    timetable = _timetable()
    fixes = [
        _obs(timetable, 21.005, _at(DAY, 8, 3, 0)),  # halfway to stop b
        _obs(timetable, 21.015, _at(DAY, 8, 8, 0)),  # halfway to stop c, 5 minutes later
    ]
    _, arrivals, _ = _run(timetable, fixes)

    assert [event["stop_id"] for event in arrivals] == ["b"]
    event = arrivals[0]
    assert event["stop_seq"] == 2
    # b lies exactly in the middle of the two fixes: 08:05:30, scheduled 08:05:00.
    assert event["actual_at"] == _at(DAY, 8, 5, 30)
    assert event["scheduled_at"] == _at(DAY, 8, 5, 0)
    assert event["delay_s"] == 30
    assert event["gap_s"] == 300


def test_each_stop_is_reported_once() -> None:
    timetable = _timetable()
    fixes = [
        _obs(timetable, 21.005, _at(DAY, 8, 3)),
        _obs(timetable, 21.015, _at(DAY, 8, 6)),
        _obs(timetable, 21.0151, _at(DAY, 8, 6, 20)),
        _obs(timetable, 21.025, _at(DAY, 8, 9)),
    ]
    _, arrivals, _ = _run(timetable, fixes)

    assert [event["stop_id"] for event in arrivals] == ["b", "c"]


def test_a_long_gap_produces_no_arrival_events() -> None:
    timetable = _timetable()
    fixes = [_obs(timetable, 21.005, _at(DAY, 8, 3)), _obs(timetable, 21.025, _at(DAY, 8, 15))]
    _, arrivals, state = _run(timetable, fixes)

    assert arrivals == []
    assert state["V/9/1"].emitted_upto == 2  # the skipped stops are not reported later either


def test_same_timestamp_is_not_recorded_twice() -> None:
    timetable = _timetable()
    state: dict[str, VehicleState] = {}
    fix = _obs(timetable, 21.012, _at(DAY, 8, 6))
    first = derive_poll([fix], lambda ids: {timetable.trip_id: timetable}, state, None)
    second = derive_poll([fix], lambda ids: {timetable.trip_id: timetable}, state, None)

    assert len(first.observations) == 1
    assert second.observations == [] and second.skipped_duplicates == 1


def test_after_midnight_times_belong_to_the_previous_service_day() -> None:
    # A trip of the 13th scheduled at 24:10, 24:15, 24:20, 24:25 runs on the 14th around 00:10.
    base = 24 * 3600
    timetable = _timetable((base + 600, base + 900, base + 1200, base + 1500))
    fixes = [
        _obs(timetable, 21.005, _at(date(2026, 10, 14), 0, 12, 30)),
        _obs(timetable, 21.015, _at(date(2026, 10, 14), 0, 18, 30)),
    ]
    rows, arrivals, _ = _run(timetable, fixes)

    assert rows[0]["service_date"] == DAY
    assert rows[0]["delay_s"] == 0
    assert arrivals[0]["scheduled_at"] == _at(date(2026, 10, 14), 0, 15)
    assert arrivals[0]["delay_s"] == 30


def test_delay_is_correct_on_the_dst_change_night() -> None:
    # 25 October 2026 has 25 hours: the 02:00-03:00 hour happens twice. A trip scheduled at
    # 6 h after the service-day origin (01:00 CEST) runs at 06:00 CET; a naive "midnight +
    # seconds" or wall-clock calculation would be an hour off.
    day = date(2026, 10, 25)
    timetable = _timetable((6 * 3600, 6 * 3600 + 300, 6 * 3600 + 600, 6 * 3600 + 900), day=day)
    on_time = service_instant(day, 6 * 3600 + 450)  # halfway between stops b and c
    rows, _, _ = _run(timetable, [_obs(timetable, 21.015, on_time + timedelta(seconds=45))])

    assert on_time == _at(day, 6, 7, 30)
    assert rows[0]["delay_s"] == 45


def test_trips_without_a_timetable_are_marked_unknown_and_keep_their_line() -> None:
    state: dict[str, VehicleState] = {}
    obs = Observation(
        "V/9/1", None, "2026-10-13:9:X:1:0800", 52.23, 21.01, None, None, _at(DAY, 8, 6)
    )
    result = derive_poll(
        [obs], lambda ids: {}, state, None, resolve_line=lambda o: ("9", "tram", "9")
    )

    row = result.observations[0]
    assert row["match_status"] == MatchStatus.TRIP_UNKNOWN
    assert (row["line"], row["mode"], row["route_id"]) == ("9", "tram", "9")


def test_observation_without_a_trip_is_stored_as_unmatched() -> None:
    obs = Observation("V/9/1", None, None, 52.23, 21.01, None, None, _at(DAY, 8, 6))
    result = derive_poll([obs], lambda ids: {}, {}, None)

    assert result.observations[0]["match_status"] == MatchStatus.NO_TRIP


def test_loop_line_projection_stays_on_the_visit_ahead_of_the_vehicle() -> None:
    # A loop: out along 21.00 -> 21.02 and back to 21.00. Position 21.01 matches both legs.
    loop = TripTimetable(
        "t", "9", "9", "bus", DAY, ("a", "b", "c", "b", "a"), (52.23,) * 5,
        (21.00, 21.01, 21.02, 21.01, 21.00), (0, 300, 600, 900, 1200),
    )  # fmt: skip
    outbound = project(52.23, 21.015, loop, last_progress=0.5)
    returning = project(52.23, 21.015, loop, last_progress=2.5)

    assert outbound is not None and returning is not None
    assert int(outbound.progress) == 1 and int(returning.progress) == 2
