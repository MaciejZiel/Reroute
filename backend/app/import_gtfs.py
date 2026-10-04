"""Import the current Warsaw bus and tram timetable into the local database."""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from .data_sources import DEFAULT_GTFS_URL, download_schedule, import_schedule, parse_schedule
from .database import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Import a previously downloaded GTFS archive")
    parser.add_argument(
        "--force-download", action="store_true", help="Replace the local archive first"
    )
    args = parser.parse_args()

    archive_path = args.file or download_schedule(
        os.getenv("GTFS_URL", DEFAULT_GTFS_URL),
        force=args.force_download,
    )
    schedule = parse_schedule(archive_path, downloaded_at=datetime.now(UTC))
    if not schedule.routes or not schedule.stops:
        raise SystemExit("GTFS archive contains no importable bus or tram network")
    with SessionLocal() as session:
        imported_routes = import_schedule(session, schedule)
    print(
        f"Imported {imported_routes} routes, {len(schedule.stops)} stops, "
        f"and {len(schedule.route_stops)} route-stop connections from {archive_path}."
    )


if __name__ == "__main__":
    main()
