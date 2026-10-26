"""Collector settings, read from the environment."""

import os
from dataclasses import dataclass

from ..data_sources import DEFAULT_VEHICLES_URL

DEFAULT_ALERTS_URL = "https://mkuran.pl/gtfs/warsaw/alerts.pb"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    vehicles_url: str = DEFAULT_VEHICLES_URL
    alerts_url: str = DEFAULT_ALERTS_URL
    poll_interval_s: float = 20.0
    alerts_interval_s: float = 60.0
    observation_retention_days: int = 7
    arrival_retention_days: int = 90
    raw_archive_dir: str = ""
    store_unmatched: bool = True
    metrics_port: int = 9100
    # Re-import the static timetable when it is older than this; 0 turns it off.
    schedule_refresh_hours: float = 24.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            vehicles_url=os.getenv("GTFS_RT_URL", DEFAULT_VEHICLES_URL),
            alerts_url=os.getenv("GTFS_ALERTS_URL", DEFAULT_ALERTS_URL),
            poll_interval_s=max(float(os.getenv("POLL_INTERVAL_S", "20")), 10.0),
            alerts_interval_s=max(float(os.getenv("ALERTS_INTERVAL_S", "60")), 30.0),
            observation_retention_days=int(os.getenv("OBSERVATION_RETENTION_DAYS", "7")),
            arrival_retention_days=int(os.getenv("ARRIVAL_RETENTION_DAYS", "90")),
            raw_archive_dir=os.getenv("RAW_ARCHIVE_DIR", ""),
            store_unmatched=_bool("STORE_UNMATCHED", True),
            metrics_port=int(os.getenv("METRICS_PORT", "9100")),
            schedule_refresh_hours=max(float(os.getenv("SCHEDULE_REFRESH_HOURS", "24")), 0.0),
        )
