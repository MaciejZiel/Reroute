"""wait_for_database retries while the database is starting and gives up eventually."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app.database import wait_for_database


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_):
        return None


class _FlakyEngine:
    """Refuses the first ``failures`` connections, like a server that is still starting."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise OperationalError("connect", {}, Exception("connection refused"))
        return _Connection()


def test_retries_until_the_database_answers() -> None:
    engine = _FlakyEngine(failures=3)
    waits: list[float] = []
    wait_for_database(engine, attempts=5, delay_s=1.0, sleep=waits.append)  # type: ignore[arg-type]
    assert engine.calls == 4
    assert waits == [1.0, 2.0, 3.0]


def test_gives_up_after_the_last_attempt() -> None:
    engine = _FlakyEngine(failures=99)
    with pytest.raises(OperationalError):
        wait_for_database(engine, attempts=3, delay_s=0.0, sleep=lambda _: None)  # type: ignore[arg-type]
    assert engine.calls == 3


def test_unreachable_server_is_reported() -> None:
    engine = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    with pytest.raises(OperationalError):
        wait_for_database(engine, attempts=1, sleep=lambda _: None)
