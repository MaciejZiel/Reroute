"""Import the current Warsaw bus and tram timetable into the local database."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from .data_sources import DEFAULT_GTFS_URL, download_schedule, import_schedule, parse_schedule
from .database import AdminSession, admin_engine

# One timetable import at a time: a second one (another collector after a
# restart, or a manual run) exits instead of downloading and parsing again.
IMPORT_LOCK_KEY = 7_201_026


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Import a previously downloaded GTFS archive")
    parser.add_argument(
        "--force-download", action="store_true", help="Replace the local archive first"
    )
    args = parser.parse_args()

    # Autocommit: the session-level lock outlives no transaction, so this
    # connection is never left idle in a transaction while the import runs.
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY}):
            print("Another timetable import is running; nothing to do.")
            return
        try:
            _import(args.file, args.force_download)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})


def _import(file: Path | None, force_download: bool) -> None:
    archive_path = file or download_schedule(
        os.getenv("GTFS_URL", DEFAULT_GTFS_URL),
        force=force_download,
    )
    schedule = parse_schedule(archive_path, downloaded_at=datetime.now(UTC))
    if not schedule.routes or not schedule.stops:
        raise SystemExit("GTFS archive contains no importable bus or tram network")
    with AdminSession() as session:
        imported_routes = import_schedule(session, schedule)
    print(
        f"Imported {imported_routes} routes, {len(schedule.stops)} stops, "
        f"{len(schedule.route_stops)} route-stop connections and {len(schedule.trips)} trips "
        f"from {archive_path}."
    )


if __name__ == "__main__":
    main()
