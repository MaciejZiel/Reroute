"""Stops: name search and the stations (platforms of one named stop) behind the map."""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .geo import distance_meters
from .models import FeedMetadata, Route, RouteStop, Stop

STATION_RADIUS_M = 400
_FEED_ID = "warsaw-static-gtfs"


@dataclass(frozen=True)
class StopInfo:
    id: str
    name: str
    latitude: float
    longitude: float
    is_demo: bool

    @property
    def platform(self) -> str | None:
        """Platform number from a Warsaw stop id (``701301`` is platform ``01`` of stop 7013)."""
        return self.id[4:] if len(self.id) == 6 and self.id.isdigit() else None


def fold(text: str) -> str:
    """Lower-case and strip diacritics, so ``Łazienkowska`` matches ``lazienkowska``."""
    text = text.casefold().replace("ł", "l")
    return "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )


class StopIndex:
    """All stops of one network (demo or imported) in memory: ~7k rows, rebuilt per import."""

    def __init__(self, stops: list[StopInfo]):
        self.by_id = {stop.id: stop for stop in stops}
        self.by_name: dict[str, list[StopInfo]] = {}
        for stop in stops:
            self.by_name.setdefault(stop.name, []).append(stop)
        self.folded = [(fold(name), name) for name in self.by_name]

    def station(self, stop: StopInfo) -> list[StopInfo]:
        """The platforms of the stop: same name, within 400 m."""
        return sorted(
            (
                other
                for other in self.by_name[stop.name]
                if distance_meters(stop.latitude, stop.longitude, other.latitude, other.longitude)
                <= STATION_RADIUS_M
            ),
            key=lambda item: item.id,
        )

    def stations_named(self, name: str) -> list[list[StopInfo]]:
        """Platforms of one name grouped into stations, nearest-first greedy clustering."""
        remaining = sorted(self.by_name[name], key=lambda item: item.id)
        groups: list[list[StopInfo]] = []
        while remaining:
            seed = remaining[0]
            group = [
                other
                for other in remaining
                if distance_meters(seed.latitude, seed.longitude, other.latitude, other.longitude)
                <= STATION_RADIUS_M
            ]
            groups.append(group)
            taken = {item.id for item in group}
            remaining = [item for item in remaining if item.id not in taken]
        return groups

    def search(self, query: str, limit: int = 8) -> list[list[StopInfo]]:
        """Stations whose name starts with, or has a word starting with, the query."""
        needle = fold(query).strip()
        if not needle:
            return []
        ranked: list[tuple[int, str, str]] = []
        for folded, name in self.folded:
            if folded.startswith(needle):
                ranked.append((0, folded, name))
            elif re.search(rf"(?:^|[\s\-/.]){re.escape(needle)}", folded):
                ranked.append((1, folded, name))
            elif needle in folded:
                ranked.append((2, folded, name))
        ranked.sort()
        stations: list[list[StopInfo]] = []
        for _, _, name in ranked:
            stations.extend(self.stations_named(name))
            if len(stations) >= limit:
                break
        return stations[:limit]


_cache: dict[bool, tuple[object, StopIndex]] = {}
_lock = threading.Lock()


def stop_index(db: Session, is_demo: bool) -> StopIndex:
    version = (
        "demo"
        if is_demo
        else db.scalar(select(FeedMetadata.downloaded_at).where(FeedMetadata.id == _FEED_ID))
    )
    cached = _cache.get(is_demo)
    if cached and cached[0] == version:
        return cached[1]
    with _lock:
        cached = _cache.get(is_demo)
        if cached and cached[0] == version:
            return cached[1]
        rows = db.execute(
            select(Stop.id, Stop.name, Stop.latitude, Stop.longitude).where(
                Stop.is_demo.is_(is_demo)
            )
        ).all()
        index = StopIndex([StopInfo(*row, is_demo) for row in rows])
        _cache[is_demo] = (version, index)
        return index


def lines_at(db: Session, stop_ids: list[str], is_demo: bool) -> list[tuple[str, str, str, str]]:
    """(route id, short name, long name, mode) of every line calling at the stops."""
    rows = db.execute(
        select(Route.id, Route.short_name, Route.long_name, Route.mode)
        .join(RouteStop, RouteStop.route_id == Route.id)
        .where(RouteStop.stop_id.in_(stop_ids), Route.is_demo.is_(is_demo))
        .distinct()
    ).all()
    return sorted((tuple(row) for row in rows), key=lambda row: natural_key(row[1]))


def natural_key(label: str) -> tuple:
    """Sort 2, 10, 175, N63 by number first, then letters."""
    match = re.match(r"^(\D*)(\d+)(.*)$", label)
    if not match:
        return (1, label, 0, "")
    prefix, number, rest = match.groups()
    return (0 if not prefix else 1, prefix, int(number), rest)


def centroid(stops: list[StopInfo]) -> tuple[float, float]:
    return (
        sum(stop.latitude for stop in stops) / len(stops),
        sum(stop.longitude for stop in stops) / len(stops),
    )
