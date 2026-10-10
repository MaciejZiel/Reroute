"""Record per-line delay snapshots from the live feed without keeping a browser open."""

import argparse
import time

from .data_sources import get_live_vehicles
from .database import SessionLocal
from .delays import SNAPSHOT_INTERVAL_S, delays_for_snapshot, summarise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=SNAPSHOT_INTERVAL_S, help="Seconds")
    parser.add_argument("--once", action="store_true", help="Record one snapshot and exit")
    args = parser.parse_args()
    while True:
        with SessionLocal() as session:
            snapshot = get_live_vehicles(session, force=True)
            delays = delays_for_snapshot(session, snapshot)
            lines = summarise(delays)
        stamp = snapshot.observed_at.isoformat() if snapshot.observed_at else "no feed"
        print(
            f"{stamp}: {len(snapshot.vehicles)} vehicles, {len(delays)} matched, {len(lines)} lines"
        )
        if args.once:
            return
        time.sleep(max(args.interval, SNAPSHOT_INTERVAL_S))


if __name__ == "__main__":
    main()
