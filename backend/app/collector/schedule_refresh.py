"""Keep the static timetable current: re-import it in a child process when it gets old.

Realtime trip ids only match the timetable they were published with. The Warsaw
feed is republished every day and its trip ids change, so a timetable imported
once stops matching after a few days and every vehicle loses its delay. The
collector checks the age of the imported timetable and, when it is older than
``refresh_hours``, or when vehicles stop matching it (a new upstream timetable)
and it is at least an hour old, runs ``python -m app.import_gtfs --force-download``
in a child process; the import itself takes a database lock, so a second one
exits at once. Polling goes on meanwhile: the import replaces the timetable in one
transaction and the pipeline reloads it when the feed version changes.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

IMPORT_COMMAND = (sys.executable, "-m", "app.import_gtfs", "--force-download")
RETRY_AFTER = timedelta(hours=1)
# A timetable no vehicle matches is replaced once it is at least this old.
UNMATCHED_MIN_AGE = timedelta(hours=1)


class Process(Protocol):
    def poll(self) -> int | None: ...


class ScheduleRefresher:
    """Start one timetable import at a time once the timetable is older than the limit."""

    def __init__(
        self,
        refresh_hours: float,
        downloaded_at: Callable[[], datetime | None],
        spawn: Callable[[], Process] | None = None,
    ) -> None:
        self.refresh_after = timedelta(hours=refresh_hours)
        self.downloaded_at = downloaded_at
        self.spawn = spawn or (lambda: subprocess.Popen(IMPORT_COMMAND))
        self.process: Process | None = None
        self.not_before: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.refresh_after > timedelta(0)

    def age(self, now: datetime) -> timedelta | None:
        """How old the imported timetable is, or None when none was imported."""
        downloaded = self.downloaded_at()
        if downloaded is None:
            return None
        if downloaded.tzinfo is None:
            downloaded = downloaded.replace(tzinfo=UTC)
        return now - downloaded

    def tick(self, now: datetime, unmatched: bool = False) -> bool:
        """Check the running import and start one if due; True when one was started.

        ``unmatched`` says the vehicles have stopped matching the timetable.
        """
        if not self.enabled:
            return False
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                return False  # still importing
            self.process = None
            if code == 0:
                logger.info("timetable import finished")
            else:
                logger.warning("timetable import failed with exit code %s", code)
            # At most one import an hour, also if a finished one left the age unchanged.
            self.not_before = now + RETRY_AFTER
        if self.not_before is not None and now < self.not_before:
            return False
        age = self.age(now)
        if age is None:
            return False
        if age < self.refresh_after and not (unmatched and age >= UNMATCHED_MIN_AGE):
            return False
        logger.info("timetable is %.1f h old, importing a fresh one", age.total_seconds() / 3600)
        try:
            self.process = self.spawn()
        except OSError:
            logger.exception("could not start the timetable import")
            self.not_before = now + RETRY_AFTER
            return False
        self.not_before = None
        return True
