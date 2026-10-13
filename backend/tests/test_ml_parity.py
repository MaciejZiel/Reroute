"""Live model inputs equal the inputs training builds (needs the out-of-tree training libraries)."""

import math
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

pytest.importorskip("numpy", reason="training libraries are installed out of tree (see README)")

from app.collector import store  # noqa: E402
from app.collector.derive import PollResult  # noqa: E402
from app.ml.featurize import FEATURE_NAMES, Event, TargetStop  # noqa: E402
from app.ml.live import load_index, model_inputs  # noqa: E402
from ml import dataset  # noqa: E402

DAY = date(2026, 10, 13)
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def _row(trip: str, seq: int, delay: int, minute: float) -> dict:
    scheduled = T0 + timedelta(minutes=minute)
    return {
        "service_date": DAY,
        "trip_id": f"2026-10-13:9:X:{trip}",
        "stop_seq": seq,
        "stop_id": f"s{seq}",
        "line": "9",
        "scheduled_at": scheduled,
        "actual_at": scheduled + timedelta(seconds=delay),
        "delay_s": delay,
        "gap_s": 30,
    }


def _events(rows: list[dict]) -> list[Event]:
    return [
        Event(
            r["trip_id"],
            r["stop_seq"],
            r["stop_id"],
            r["line"],
            r["scheduled_at"].timestamp(),
            r["actual_at"].timestamp(),
            r["delay_s"],
            r["actual_at"].timestamp() + r["gap_s"],
        )
        for r in rows
    ]


def _observations(rows: list[dict]) -> dataset.Observations:
    """A vehicle report every 20 s from the first to the last arrival of each trip."""
    series: dict[str, tuple[list[float], list[int]]] = {}
    for trip in {r["trip_id"] for r in rows}:
        mine = [r for r in rows if r["trip_id"] == trip]
        start = min(r["actual_at"] for r in mine).timestamp()
        end = max(r["actual_at"] for r in mine).timestamp()
        times = [start + 20 * i for i in range(int((end - start) // 20) + 1)]
        series[trip] = (times, [int(30 + (t - start) / 7) for t in times])
    return dataset.Observations(series)


def test_live_inputs_equal_training_inputs_at_arbitrary_moments_with_a_vehicle_delay(
    db: Session, pg_engine: Engine
) -> None:
    rows = [
        _row(trip, seq, 15 * seq + 40 * n + (seq % 3) * 7, offset + 3 * seq)
        for n, (trip, offset) in enumerate([("a", 0), ("b", 9), ("c", 21)])
        for seq in range(1, 8)
    ]
    store.write_poll(pg_engine, PollResult(arrivals=rows))
    events = _events(rows)
    observations = _observations(rows)
    samples = dataset.build_samples(events, lags=(1, 2, 4), observations=observations)
    assert len(samples) > 20
    assert (samples.X[:, FEATURE_NAMES.index("seconds_since_origin")] > 0).any()

    checked = 0
    for i in range(0, len(samples), 3):
        trip, stop = samples.trip_id[i], samples.stop_id[i]
        target = next(e for e in events if e.trip_id == trip and e.stop_id == stop)
        moment = float(samples.t0[i])
        now = datetime.fromtimestamp(moment, UTC)
        times, delays = observations.rows[trip]
        newest = max(k for k, t in enumerate(times) if t <= moment)
        index = load_index(db, [trip], ["9"], [stop], now)
        (live,) = model_inputs(
            index,
            [TargetStop(trip, target.stop_seq, stop, "9", target.scheduled_s)],
            now,
            [delays[newest]],
            [moment - times[newest]],
        )
        assert live is not None
        for name, expected in zip(FEATURE_NAMES, samples.X[i], strict=True):
            got = live[name]
            assert got == pytest.approx(expected, abs=1e-6) or (
                math.isnan(got) and math.isnan(expected)
            ), (name, got, expected)
        checked += 1
    assert checked > 5
