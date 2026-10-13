"""Replay archived raw snapshots (``<dir>/<YYYY-MM-DD>/v_<HHMMSS>.pb.gz``) through the pipeline.

Incremental: the last processed file is stored in ``collector_state`` per archive directory, so
a repeated run (for example from cron) only reads files that arrived since. Writes are
idempotent, so overlapping runs or a re-run of a day are harmless.
"""

from __future__ import annotations

import gzip
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from gtfs_rt_kit import GtfsRtKitError, parse_feed
from sqlalchemy import Engine

from . import store
from .derive import MatchStatus
from .service import Pipeline

logger = logging.getLogger(__name__)
SOURCE_BACKFILL = 1
CHECKPOINT_EVERY = 25


@dataclass
class BackfillReport:
    files: int = 0
    unreadable: int = 0
    observations: int = 0
    arrivals: int = 0
    trip_unknown: int = 0
    last_file: str | None = None


def _state_key(raw_dir: Path) -> str:
    return f"backfill:{raw_dir.resolve()}"


def list_snapshots(
    raw_dir: Path, start: date | None = None, end: date | None = None
) -> Iterator[tuple[str, Path]]:
    """Snapshot files in time order as (relative name, path)."""
    for day_dir in sorted(path for path in raw_dir.iterdir() if path.is_dir()):
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if (start and day < start) or (end and day > end):
            continue
        for path in sorted(day_dir.glob("v_*.pb.gz")):
            yield f"{day_dir.name}/{path.name}", path


def run_backfill(
    engine: Engine,
    pipeline: Pipeline,
    raw_dir: Path,
    start: date | None = None,
    end: date | None = None,
    resume: bool = True,
) -> BackfillReport:
    report = BackfillReport()
    last_done = store.get_state(engine, _state_key(raw_dir)) if resume else None
    pipeline.refresh_static()
    for name, path in list_snapshots(raw_dir, start, end):
        if last_done and name <= last_done:
            if name == last_done:
                _prime(pipeline, path)
            continue
        try:
            feed = parse_feed(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError, GtfsRtKitError) as error:
            logger.warning("skipping unreadable snapshot %s: %s", name, error)
            report.unreadable += 1
            continue
        result = pipeline.process(feed, SOURCE_BACKFILL)
        report.files += 1
        report.observations += len(result.observations)
        report.arrivals += len(result.arrivals)
        report.trip_unknown += sum(
            row["match_status"] == MatchStatus.TRIP_UNKNOWN for row in result.observations
        )
        report.last_file = name
        if report.files % CHECKPOINT_EVERY == 0:
            store.set_state(engine, _state_key(raw_dir), name)
    if report.last_file:
        store.set_state(engine, _state_key(raw_dir), report.last_file)
    return report


def _prime(pipeline: Pipeline, path: Path) -> None:
    """Rebuild per-vehicle progress from the last processed snapshot without writing."""
    try:
        pipeline.derive(parse_feed(gzip.decompress(path.read_bytes())), SOURCE_BACKFILL)
    except (OSError, EOFError, GtfsRtKitError):
        pass
