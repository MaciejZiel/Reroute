"""Command line entry point: ``python -m app.collector run | backfill | housekeeping``."""

import argparse
import logging
from datetime import UTC, date, datetime
from pathlib import Path

from ..database import engine
from ..migrate import migrate
from . import store
from .backfill import run_backfill
from .config import Settings
from .service import Pipeline, run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.collector", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("run", help="poll the realtime feeds until stopped")
    live.add_argument("--once", action="store_true", help="poll once and exit")
    back = commands.add_parser("backfill", help="replay raw gzipped vehicles.pb snapshots")
    back.add_argument("--raw-dir", type=Path, required=True)
    back.add_argument("--from", dest="start", type=date.fromisoformat)
    back.add_argument("--to", dest="end", type=date.fromisoformat)
    back.add_argument(
        "--no-resume", action="store_true", help="ignore the stored position and start over"
    )
    commands.add_parser("housekeeping", help="create upcoming partitions and drop expired ones")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    migrate(engine)
    if args.command == "run":
        run(engine, settings, once=args.once)
    elif args.command == "backfill":
        pipeline = Pipeline(engine, settings)
        report = run_backfill(
            engine, pipeline, args.raw_dir, args.start, args.end, resume=not args.no_resume
        )
        print(
            f"Backfilled {report.files} snapshot(s): {report.observations} observations, "
            f"{report.arrivals} arrival events, {report.trip_unknown} observations of trips "
            f"missing from the timetable, {report.unreadable} unreadable file(s). "
            f"Last file: {report.last_file or 'none (nothing new)'}."
        )
    else:
        dropped = store.housekeeping(
            engine,
            datetime.now(UTC),
            settings.observation_retention_days,
            settings.arrival_retention_days,
        )
        print(f"Dropped {len(dropped)} expired partition(s): {', '.join(dropped) or '-'}")


if __name__ == "__main__":
    main()
