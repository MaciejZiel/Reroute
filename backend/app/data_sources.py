"""Download, parse and persist public Warsaw transit feeds."""

import csv
import io
import os
import tempfile
import threading
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import httpx
from geoalchemy2.shape import from_shape
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2
from shapely.geometry import LineString, MultiLineString, Point
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import FeedMetadata, Route, RouteStop, Stop

DEFAULT_GTFS_URL = "https://mkuran.pl/gtfs/warsaw.zip"
DEFAULT_VEHICLES_URL = "https://mkuran.pl/gtfs/warsaw/vehicles.pb"
VEHICLE_REFRESH_SECONDS = 12
WARSAW_BOUNDS = (20.3, 51.7, 21.8, 52.7)


@dataclass(frozen=True)
class ParsedStop:
    id: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ParsedRoute:
    id: str
    short_name: str
    long_name: str
    mode: str
    shapes: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True)
class ParsedRouteStop:
    route_id: str
    stop_id: str
    sequence: int


@dataclass(frozen=True)
class ParsedSchedule:
    stops: tuple[ParsedStop, ...]
    routes: tuple[ParsedRoute, ...]
    route_stops: tuple[ParsedRouteStop, ...]
    downloaded_at: datetime
    feed_start_date: str
    feed_end_date: str
    feed_version: str
    attributions: str


@dataclass(frozen=True)
class LiveVehicle:
    id: str
    line: str
    mode: str
    latitude: float
    longitude: float
    observed_at: datetime
    is_stale: bool


@dataclass(frozen=True)
class LiveVehicleSnapshot:
    vehicles: tuple[LiveVehicle, ...]
    observed_at: datetime | None
    error: str | None = None


def download_schedule(
    url: str = DEFAULT_GTFS_URL, data_dir: Path | None = None, force: bool = False
) -> Path:
    """Download the current static feed once and keep a local copy for repeat imports."""
    target_dir = data_dir or Path(os.getenv("DATA_DIR", "data"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "warsaw-gtfs.zip"
    if target.exists() and target.stat().st_size > 0 and not force:
        return target

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target_dir, suffix=".part", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with httpx.stream(
                "GET", url, timeout=httpx.Timeout(90.0, connect=10.0), follow_redirects=True
            ) as response:
                response.raise_for_status()
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 300_000_000:
                        raise ValueError("GTFS archive exceeded the 300 MB download limit")
                    temporary.write(chunk)
        with zipfile.ZipFile(temporary_path) as archive:
            if "routes.txt" not in archive.namelist() or "stops.txt" not in archive.namelist():
                raise ValueError("The downloaded archive is not a valid GTFS schedule")
        temporary_path.replace(target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return target


def parse_schedule(path: Path, downloaded_at: datetime | None = None) -> ParsedSchedule:
    """Read bus and tram network data from a GTFS archive without extracting it."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {"routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "shapes.txt"}
        if missing := required - names:
            raise ValueError(
                f"GTFS archive is missing required files: {', '.join(sorted(missing))}"
            )

        stops: dict[str, ParsedStop] = {}
        with _csv_rows(archive, "stops.txt") as rows:
            for row in rows:
                stop_id = row.get("stop_id", "").strip()
                name = row.get("stop_name", "").strip()
                try:
                    latitude = float(row["stop_lat"])
                    longitude = float(row["stop_lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                if stop_id and name and -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    stops[stop_id] = ParsedStop(stop_id, name, latitude, longitude)

        routes: dict[str, ParsedRoute] = {}
        with _csv_rows(archive, "routes.txt") as rows:
            for row in rows:
                route_id = row.get("route_id", "").strip()
                mode = _route_mode(row.get("route_type", ""))
                if route_id and mode:
                    routes[route_id] = ParsedRoute(
                        route_id,
                        row.get("route_short_name", "").strip() or route_id,
                        row.get("route_long_name", "").strip(),
                        mode,
                        (),
                    )

        trip_routes: dict[str, tuple[str, str]] = {}
        shape_counts: Counter[tuple[str, str]] = Counter()
        with _csv_rows(archive, "trips.txt") as rows:
            for row in rows:
                route_id = row.get("route_id", "").strip()
                trip_id = row.get("trip_id", "").strip()
                shape_id = row.get("shape_id", "").strip()
                if trip_id and route_id in routes:
                    trip_routes[trip_id] = (route_id, shape_id)
                    if shape_id:
                        shape_counts[(route_id, shape_id)] += 1

        selected_shapes: dict[str, set[str]] = defaultdict(set)
        for (route_id, shape_id), _ in shape_counts.most_common():
            if len(selected_shapes[route_id]) < 2:
                selected_shapes[route_id].add(shape_id)

        route_stop_sequences: dict[tuple[str, str], int] = {}
        with _csv_rows(archive, "stop_times.txt") as rows:
            for row in rows:
                trip = trip_routes.get(row.get("trip_id", ""))
                stop_id = row.get("stop_id", "").strip()
                if (
                    trip is None
                    or stop_id not in stops
                    or (trip[1] and trip[1] not in selected_shapes[trip[0]])
                ):
                    continue
                try:
                    sequence = int(row["stop_sequence"])
                except (KeyError, TypeError, ValueError):
                    continue
                key = (trip[0], stop_id)
                route_stop_sequences[key] = min(sequence, route_stop_sequences.get(key, sequence))

        shape_points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        wanted_shapes = set().union(*selected_shapes.values()) if selected_shapes else set()
        with _csv_rows(archive, "shapes.txt") as rows:
            for row in rows:
                shape_id = row.get("shape_id", "").strip()
                if shape_id not in wanted_shapes:
                    continue
                try:
                    sequence = int(row["shape_pt_sequence"])
                    latitude = float(row["shape_pt_lat"])
                    longitude = float(row["shape_pt_lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                shape_points[shape_id].append((sequence, longitude, latitude))

        parsed_routes = []
        for route_id, route in routes.items():
            lines = tuple(
                tuple(
                    (longitude, latitude)
                    for _, longitude, latitude in sorted(shape_points[shape_id])
                )
                for shape_id in sorted(selected_shapes[route_id])
                if len(shape_points[shape_id]) >= 2
            )
            parsed_routes.append(
                ParsedRoute(route.id, route.short_name, route.long_name, route.mode, lines)
            )

        feed_info = _read_first_row(archive, "feed_info.txt") if "feed_info.txt" in names else {}
        attributions = ""
        if "attributions.txt" in names:
            with archive.open("attributions.txt") as source:
                attributions = source.read().decode("utf-8-sig").strip()

    route_stops = tuple(
        ParsedRouteStop(route_id, stop_id, sequence)
        for (route_id, stop_id), sequence in route_stop_sequences.items()
    )
    routes_with_stops = {item.route_id for item in route_stops}
    parsed_routes = [route for route in parsed_routes if route.id in routes_with_stops]
    retained_ids = {route.id for route in parsed_routes}
    route_stops = tuple(item for item in route_stops if item.route_id in retained_ids)
    return ParsedSchedule(
        stops=tuple(stops.values()),
        routes=tuple(parsed_routes),
        route_stops=route_stops,
        downloaded_at=downloaded_at or datetime.now(UTC),
        feed_start_date=feed_info.get("feed_start_date", ""),
        feed_end_date=feed_info.get("feed_end_date", ""),
        feed_version=feed_info.get("feed_version", ""),
        attributions=attributions,
    )


def import_schedule(session: Session, schedule: ParsedSchedule) -> int:
    """Replace only live GTFS records and preserve the fictional demo fallback."""
    live_route_ids = select(Route.id).where(Route.is_demo.is_(False))
    session.execute(delete(RouteStop).where(RouteStop.route_id.in_(live_route_ids)))
    session.execute(delete(Route).where(Route.is_demo.is_(False)))
    session.execute(delete(Stop).where(Stop.is_demo.is_(False)))
    session.flush()

    session.add_all(
        Stop(
            id=stop.id,
            name=stop.name,
            latitude=stop.latitude,
            longitude=stop.longitude,
            location=from_shape(Point(stop.longitude, stop.latitude), srid=4326),
            is_demo=False,
        )
        for stop in schedule.stops
    )
    session.flush()
    session.add_all(
        Route(
            id=route.id,
            short_name=route.short_name,
            long_name=route.long_name,
            mode=route.mode,
            shape=_route_shape(route.shapes),
            is_demo=False,
        )
        for route in schedule.routes
    )
    session.flush()
    session.add_all(
        RouteStop(route_id=item.route_id, stop_id=item.stop_id, sequence=item.sequence)
        for item in schedule.route_stops
    )
    session.merge(
        FeedMetadata(
            id="warsaw-static-gtfs",
            downloaded_at=schedule.downloaded_at,
            feed_start_date=schedule.feed_start_date,
            feed_end_date=schedule.feed_end_date,
            feed_version=schedule.feed_version,
            attributions=schedule.attributions,
        )
    )
    session.commit()
    return len(schedule.routes)


_vehicle_cache_lock = threading.Lock()
_vehicle_cache_until = 0.0
_vehicle_cache = LiveVehicleSnapshot((), None, "not fetched")


def get_live_vehicles(session: Session, force: bool = False) -> LiveVehicleSnapshot:
    """Fetch and cache the public GTFS-Realtime positions feed briefly."""
    global _vehicle_cache_until, _vehicle_cache
    if not force and time.monotonic() < _vehicle_cache_until:
        return _vehicle_cache
    with _vehicle_cache_lock:
        if not force and time.monotonic() < _vehicle_cache_until:
            return _vehicle_cache
        try:
            url = os.getenv("GTFS_RT_URL", DEFAULT_VEHICLES_URL)
            response = httpx.get(
                url, timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=True
            )
            response.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            header_time = (
                _utc_datetime(feed.header.timestamp) if feed.header.HasField("timestamp") else None
            )
            route_rows = session.scalars(
                select(Route).order_by(Route.is_demo, Route.short_name)
            ).all()
            route_by_id = {route.id: route for route in route_rows}
            route_by_name: dict[str, Route] = {}
            for route in route_rows:
                route_by_name.setdefault(route.short_name.casefold(), route)

            vehicles: list[LiveVehicle] = []
            observed_times = []
            min_lon, min_lat, max_lon, max_lat = WARSAW_BOUNDS
            for entity in feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                vehicle = entity.vehicle
                if not vehicle.HasField("position"):
                    continue
                latitude = vehicle.position.latitude
                longitude = vehicle.position.longitude
                if not (min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon):
                    continue
                route_id = vehicle.trip.route_id if vehicle.HasField("trip") else ""
                vehicle_id = vehicle.vehicle.id or vehicle.vehicle.label or entity.id
                if not route_id and vehicle_id.startswith("V/"):
                    parts = vehicle_id.split("/")
                    route_id = parts[1] if len(parts) > 2 else ""
                route = route_by_id.get(route_id) or route_by_name.get(route_id.casefold())
                line = route.short_name if route else route_id
                if not line and vehicle.HasField("vehicle"):
                    line = vehicle.vehicle.label or vehicle.vehicle.id
                mode = route.mode if route else "bus"
                observed_at = (
                    _utc_datetime(vehicle.timestamp)
                    if vehicle.HasField("timestamp")
                    else header_time
                )
                observed_at = observed_at or datetime.now(UTC)
                observed_times.append(observed_at)
                vehicles.append(
                    LiveVehicle(
                        id=vehicle_id,
                        line=line or "?",
                        mode=mode,
                        latitude=latitude,
                        longitude=longitude,
                        observed_at=observed_at,
                        is_stale=(datetime.now(UTC) - observed_at).total_seconds() > 120,
                    )
                )
            snapshot = LiveVehicleSnapshot(
                tuple(vehicles),
                max(observed_times, default=header_time),
                None if vehicles else "empty feed",
            )
        except (httpx.HTTPError, DecodeError, ValueError) as error:
            snapshot = LiveVehicleSnapshot((), None, str(error))
        _vehicle_cache = snapshot
        _vehicle_cache_until = time.monotonic() + VEHICLE_REFRESH_SECONDS
        return snapshot


def _route_mode(route_type: str) -> str | None:
    if route_type in {"0", "900"}:
        return "tram"
    if route_type in {"3", "700"}:
        return "bus"
    return None


def _route_shape(shapes: tuple[tuple[tuple[float, float], ...], ...]) -> object | None:
    lines = [LineString(shape) for shape in shapes if len(shape) >= 2]
    if not lines:
        return None
    return from_shape(lines[0] if len(lines) == 1 else MultiLineString(lines), srid=4326)


def _utc_datetime(timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


def _read_first_row(archive: zipfile.ZipFile, filename: str) -> dict[str, str]:
    with _csv_rows(archive, filename) as rows:
        return next(rows, {})


class _CsvRows:
    def __init__(self, archive: zipfile.ZipFile, filename: str):
        self.archive = archive
        self.filename = filename
        self.source: TextIO | None = None
        self.reader: csv.DictReader | None = None

    def __enter__(self) -> csv.DictReader:
        self.source = io.TextIOWrapper(
            self.archive.open(self.filename), encoding="utf-8-sig", newline=""
        )
        self.reader = csv.DictReader(self.source)
        return self.reader

    def __exit__(self, *_: object) -> None:
        if self.source:
            self.source.close()


def _csv_rows(archive: zipfile.ZipFile, filename: str) -> _CsvRows:
    return _CsvRows(archive, filename)
