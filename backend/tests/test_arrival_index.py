"""The arrival-time index is built without blocking collector inserts, and can be rolled back."""

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from app import migrate
from app.collector import partitions

DAY = date(2026, 10, 13)


def _row(seq: int, trip: str = "T") -> dict:
    moment = datetime(2026, 10, 13, 10, 0, tzinfo=UTC) + timedelta(seconds=seq)
    return {
        "service_date": DAY,
        "trip_id": trip,
        "stop_seq": seq,
        "stop_id": "s",
        "line": "9",
        "scheduled_at": moment,
        "actual_at": moment,
        "delay_s": 0,
        "gap_s": 30,
    }


INSERT = text(
    "INSERT INTO stop_arrival_events (service_date, trip_id, stop_seq, stop_id, line, "
    "scheduled_at, actual_at, delay_s, gap_s) VALUES (:service_date, :trip_id, :stop_seq, "
    ":stop_id, :line, :scheduled_at, :actual_at, :delay_s, :gap_s)"
)


@pytest.fixture
def partitioned(pg_engine: Engine):
    with pg_engine.begin() as connection:
        partitions.ensure_arrival_partitions(connection, [DAY])
        connection.execute(text("DROP INDEX IF EXISTS ix_stop_arrival_events_actual_at"))
        connection.execute(text("TRUNCATE stop_arrival_events"))
    yield pg_engine
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE stop_arrival_events"))


def _valid_indexes(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
                    "WHERE c.relname LIKE '%actual_at%' AND x.indisvalid ORDER BY 1"
                )
            ).scalars()
        )


def test_the_index_is_built_on_every_partition_attached_and_idempotent(partitioned: Engine) -> None:
    with partitioned.begin() as connection:
        connection.execute(INSERT, [_row(n) for n in range(1, 50)])

    built = migrate.ensure_arrival_time_index(partitioned)

    assert built == ["stop_arrival_events_p202610"]
    assert migrate.ensure_arrival_time_index(partitioned) == []
    assert _valid_indexes(partitioned) == [
        "ix_stop_arrival_events_actual_at",
        "stop_arrival_events_p202610_actual_at_idx",
    ]


def test_collector_inserts_are_not_blocked_while_the_index_is_built(partitioned: Engine) -> None:
    holder = partitioned.connect()
    holder_tx = holder.begin()
    holder.execute(INSERT, _row(1000, "held"))  # an open writer: the build must wait for it
    finished = threading.Event()

    def build() -> None:
        migrate.ensure_arrival_time_index(partitioned)
        finished.set()

    thread = threading.Thread(target=build)
    thread.start()
    try:
        assert not finished.wait(1.5)  # the concurrent build is waiting for the open writer
        with partitioned.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            connection.execute(INSERT, [_row(n) for n in range(1, 30)])  # does not block
    finally:
        holder_tx.commit()
        holder.close()
        thread.join(30)
    assert finished.is_set()
    assert "stop_arrival_events_p202610_actual_at_idx" in _valid_indexes(partitioned)
    with partitioned.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM stop_arrival_events")).scalar() == 30


def test_a_partition_created_later_gets_its_index_and_a_failed_build_is_replaced(
    partitioned: Engine,
) -> None:
    migrate.ensure_arrival_time_index(partitioned)
    november = date(2026, 11, 2)
    with partitioned.begin() as connection:
        partitions.ensure_arrival_partitions(connection, [november])
    names = _valid_indexes(partitioned)
    assert any("p202611" in name for name in names)  # created with the partition, automatically

    migrate.drop_arrival_time_index(partitioned)
    with partitioned.begin() as connection:  # a half-built leftover from an interrupted run
        connection.execute(
            text(
                "CREATE INDEX stop_arrival_events_p202610_actual_at_idx "
                "ON stop_arrival_events_p202610 USING brin (actual_at)"
            )
        )
        connection.execute(
            text(
                "UPDATE pg_index SET indisvalid = false WHERE indexrelid = "
                "'stop_arrival_events_p202610_actual_at_idx'::regclass"
            )
        )
    assert migrate.ensure_arrival_time_index(partitioned) == [
        "stop_arrival_events_p202610",
        "stop_arrival_events_p202611",
    ]
    assert "stop_arrival_events_p202610_actual_at_idx" in _valid_indexes(partitioned)


def test_the_down_path_removes_the_index_and_the_old_record(partitioned: Engine) -> None:
    migrate.ensure_arrival_time_index(partitioned)
    with partitioned.begin() as connection:
        connection.execute(
            text("INSERT INTO schema_migrations (name) VALUES ('0004_arrival_time_index.sql')")
        )

    migrate.drop_arrival_time_index(partitioned)

    assert _valid_indexes(partitioned) == []
    with partitioned.connect() as connection:
        assert not connection.execute(
            text("SELECT 1 FROM schema_migrations WHERE name LIKE '0004%'")
        ).first()
    assert migrate.ensure_arrival_time_index(partitioned)  # and it can come back
