"""Network, health and punctuality endpoints."""

import csv
import gzip
import io
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from fastapi import APIRouter, Depends, Query, Request, Response
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .database import get_db
from .demo_data import demo_line_delays
from .live import ensure_fresh
from .models import (
    DelaySnapshot,
    FeedMetadata,
    Route,
    RouteStop,
    Stop,
)
from .schemas import (
    Feature,
    FeatureCollection,
    HealthResponse,
    LinePunctuality,
    PunctualityPoint,
    PunctualityResponse,
)

router = APIRouter(prefix="/api")
FEED_ID = "warsaw-static-gtfs"
EARLY_LIMIT_S = -60  # earlier than this many seconds is "early"
LATE_LIMIT_S = 180  # later than this many seconds is "late"
DEMO_NOTICE = "Demo network. Vehicles and routes are fictional and do not describe real service."


def _feature(feature_id: str, geometry: dict, properties: dict) -> Feature:
    return Feature(id=feature_id, geometry=geometry, properties=properties)


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(select(1))
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
    return HealthResponse(
        status="ok", database="connected", data_mode="live" if has_schedule else "demo"
    )


_network_cache: dict[object, tuple[bytes, bytes]] = {}


@router.get("/network", response_model=FeatureCollection, tags=["network"])
def network(request: Request, db: Session = Depends(get_db)) -> Response:
    """The whole network as GeoJSON, serialised and gzipped once per imported timetable."""
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
    key = (has_schedule, _network_version(db, not has_schedule))
    if key not in _network_cache:
        body = _network_collection(db, has_schedule).model_dump_json().encode()
        _network_cache.clear()
        _network_cache[key] = (body, gzip.compress(body, compresslevel=6))
    body, compressed = _network_cache[key]
    headers = {"Vary": "Accept-Encoding", "Cache-Control": "no-cache"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(compressed, media_type="application/json", headers=headers)
    return Response(body, media_type="application/json", headers=headers)


def _stop_modes(db: Session, is_demo: bool) -> dict[str, str]:
    """Per stop: "bus", "tram" or "both", from the modes of the routes that call there."""
    rows = db.execute(
        select(RouteStop.stop_id, Route.mode)
        .join(Route, Route.id == RouteStop.route_id)
        .where(Route.is_demo.is_(is_demo))
        .distinct()
    ).all()
    seen: dict[str, set[str]] = defaultdict(set)
    for stop_id, mode in rows:
        seen[stop_id].add(mode)
    return {
        stop_id: next(iter(found)) if len(found) == 1 else "both" for stop_id, found in seen.items()
    }


def _network_collection(db: Session, has_schedule: bool) -> FeatureCollection:
    demo_filter = not has_schedule
    routes = db.scalars(select(Route).where(Route.is_demo.is_(demo_filter))).all()
    stops = db.scalars(select(Stop).where(Stop.is_demo.is_(demo_filter))).all()
    features = [
        _feature(
            route.id,
            mapping(to_shape(route.shape)),
            {
                "entity_type": "route",
                "route_id": route.id,
                "short_name": route.short_name,
                "long_name": route.long_name,
                "mode": route.mode,
            },
        )
        for route in routes
        if route.shape is not None
    ]
    modes = _stop_modes(db, demo_filter)
    features.extend(
        _feature(
            stop.id,
            {"type": "Point", "coordinates": [stop.longitude, stop.latitude]},
            {"entity_type": "stop", "name": stop.name, "mode": modes.get(stop.id, "bus")},
        )
        for stop in stops
    )
    metadata = db.get(FeedMetadata, FEED_ID) if has_schedule else None
    notice = _schedule_notice(metadata) if has_schedule else DEMO_NOTICE
    return FeatureCollection(
        features=features,
        data_mode="live" if has_schedule else "demo",
        observed_at=None,
        notice=notice,
    )


PUNCTUALITY_MAX_MINUTES = 6 * 60
PUNCTUALITY_CACHE_SECONDS = 60.0
# Windows longer than this are summed into 5-minute buckets in SQL, so the response stays a
# few hundred KB however many snapshots lie in the window.
PUNCTUALITY_FINE_MINUTES = 60
_punctuality_cache: dict[int, tuple[float, PunctualityResponse]] = {}
_punctuality_lock = threading.Lock()


@dataclass(frozen=True)
class _Bucket:
    """The snapshots of one line in one time bucket, summed in the database."""

    observed_at: datetime
    line: str
    mode: str
    snapshots: int
    vehicles: int  # summed over the snapshots: the number of vehicle observations
    weighted_delay_s: int  # sum of mean delay * vehicles
    median_delay_s: float  # median of the snapshot medians inside the bucket
    early: int
    on_time: int
    late: int


def _bucket_rows(db: Session, since: datetime, bucket_minutes: int) -> list[_Bucket]:
    observed = DelaySnapshot.observed_at
    bucket = func.date_bin(
        text(f"interval '{int(bucket_minutes)} minutes'"),
        observed,
        text("timestamptz '2000-01-01'"),
    ).label("bucket")
    rows = db.execute(
        select(
            bucket,
            DelaySnapshot.line,
            DelaySnapshot.mode,
            func.count().label("snapshots"),
            func.sum(DelaySnapshot.vehicles).label("vehicles"),
            func.sum(DelaySnapshot.mean_delay_seconds * DelaySnapshot.vehicles).label("weighted"),
            func.percentile_cont(0.5).within_group(DelaySnapshot.median_delay_seconds).label("med"),
            func.sum(DelaySnapshot.early).label("early"),
            func.sum(DelaySnapshot.on_time).label("on_time"),
            func.sum(DelaySnapshot.late).label("late"),
        )
        .where(observed >= since)
        .group_by(bucket, DelaySnapshot.line, DelaySnapshot.mode)
        .order_by(bucket)
    ).all()
    return [
        _Bucket(
            row.bucket,
            row.line,
            row.mode,
            row.snapshots,
            int(row.vehicles),
            int(row.weighted),
            float(row.med),
            int(row.early),
            int(row.on_time),
            int(row.late),
        )
        for row in rows
    ]


@router.get("/punctuality", response_model=PunctualityResponse, tags=["vehicles"])
def punctuality(
    minutes: int = Query(default=120, ge=10, le=PUNCTUALITY_MAX_MINUTES),
    db: Session = Depends(get_db),
) -> PunctualityResponse:
    """Per-line delay history estimated from live positions against the timetable."""
    now = datetime.now(UTC)
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
    if not has_schedule:
        return _demo_punctuality(db, now, minutes)
    ensure_fresh(db)
    cached = _punctuality_cache.get(minutes)
    if cached and time.monotonic() - cached[0] < PUNCTUALITY_CACHE_SECONDS:
        return cached[1]
    with _punctuality_lock:  # one visitor computes, the others wait for the same answer
        cached = _punctuality_cache.get(minutes)
        if cached and time.monotonic() - cached[0] < PUNCTUALITY_CACHE_SECONDS:
            return cached[1]
        response = _live_punctuality(db, now, minutes)
        _punctuality_cache[minutes] = (time.monotonic(), response)
    return response


def _live_punctuality(db: Session, now: datetime, minutes: int) -> PunctualityResponse:
    since = now - timedelta(minutes=minutes)
    bucket_minutes = 1 if minutes <= PUNCTUALITY_FINE_MINUTES else 5
    buckets = _bucket_rows(db, since, bucket_minutes)
    seen = db.execute(
        select(
            func.count(func.distinct(DelaySnapshot.observed_at)),
            func.min(DelaySnapshot.observed_at),
            func.max(DelaySnapshot.observed_at),
        ).where(DelaySnapshot.observed_at >= since)
    ).one()
    return PunctualityResponse(
        data_mode="live",
        window_minutes=minutes,
        snapshots=seen[0],
        observed_from=seen[1],
        observed_to=seen[2],
        on_time_definition=_ON_TIME_DEFINITION,
        lines=_line_punctuality_buckets(buckets),
        notice=(
            "Delays estimated by projecting live vehicle positions (Miasto Stołeczne Warszawa, "
            "GTFS-Realtime by Mikołaj Kuranowski) onto the ZTM GTFS timetable of the trip "
            "each vehicle reports. Snapshots are stored at most once a minute while the app "
            "is in use."
            + (
                f" Windows over {PUNCTUALITY_FINE_MINUTES} minutes are shown in "
                f"{bucket_minutes}-minute steps."
                if bucket_minutes > 1
                else ""
            )
        ),
    )


def _demo_punctuality(db: Session, now: datetime, minutes: int) -> PunctualityResponse:
    demo_lines = db.execute(
        select(Route.short_name, Route.mode)
        .where(Route.is_demo.is_(True))
        .order_by(Route.short_name)
    ).all()
    since = now - timedelta(minutes=minutes)
    rows = [
        row
        for row in demo_line_delays(now, [tuple(line) for line in demo_lines])
        if row.observed_at >= since
    ]
    return PunctualityResponse(
        data_mode="demo",
        window_minutes=minutes,
        snapshots=len({row.observed_at for row in rows}),
        observed_from=rows[0].observed_at if rows else None,
        observed_to=rows[-1].observed_at if rows else None,
        on_time_definition=_ON_TIME_DEFINITION,
        lines=_line_punctuality(rows),
        notice=(
            "Demo network: the punctuality history is fictional. Import the Warsaw timetable "
            "to estimate real delays from live positions."
        ),
    )


_ON_TIME_DEFINITION = (
    f"On time: between {-EARLY_LIMIT_S // 60} min early and {LATE_LIMIT_S // 60} min late."
)


def _line_punctuality(rows: list[DelaySnapshot]) -> list[LinePunctuality]:
    """Per-line summary of single snapshots (one bucket per snapshot)."""
    return _line_punctuality_buckets(
        [
            _Bucket(
                row.observed_at,
                row.line,
                row.mode,
                1,
                row.vehicles,
                row.mean_delay_seconds * row.vehicles,
                float(row.median_delay_seconds),
                row.early,
                row.on_time,
                row.late,
            )
            for row in rows
        ]
    )


def _line_punctuality_buckets(buckets: list[_Bucket]) -> list[LinePunctuality]:
    by_line: dict[tuple[str, str], list[_Bucket]] = defaultdict(list)
    for bucket in buckets:
        by_line[(bucket.line, bucket.mode)].append(bucket)
    result = []
    for (line, mode), steps in by_line.items():
        observations = sum(step.vehicles for step in steps)
        if not observations:
            continue
        result.append(
            LinePunctuality(
                line=line,
                mode=mode,
                observations=observations,
                mean_delay_minutes=round(
                    sum(step.weighted_delay_s for step in steps) / observations / 60, 1
                ),
                median_delay_minutes=round(median(step.median_delay_s for step in steps) / 60, 1),
                early_share=round(sum(step.early for step in steps) / observations, 3),
                on_time_share=round(sum(step.on_time for step in steps) / observations, 3),
                late_share=round(sum(step.late for step in steps) / observations, 3),
                series=[
                    PunctualityPoint(
                        observed_at=step.observed_at,
                        vehicles=round(step.vehicles / step.snapshots),
                        mean_delay_minutes=(
                            round(step.weighted_delay_s / step.vehicles / 60, 1)
                            if step.vehicles
                            else 0.0
                        ),
                        on_time_share=(
                            round(step.on_time / step.vehicles, 3) if step.vehicles else 0
                        ),
                    )
                    for step in steps
                ],
            )
        )
    return sorted(result, key=lambda item: (-item.observations, item.line))


def _network_version(db: Session, is_demo: bool) -> object:
    if is_demo:
        return "demo"  # seeded once at startup, before any request
    return db.scalar(select(FeedMetadata.downloaded_at).where(FeedMetadata.id == FEED_ID))


def _schedule_notice(metadata: FeedMetadata | None) -> str:
    if metadata is None:
        return "Warsaw bus and tram schedule from the ZTM GTFS feed."
    downloaded_at = metadata.downloaded_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    notice = (
        "Warsaw bus and tram schedule from ZTM GTFS, distributed by Mikołaj Kuranowski. "
        f"Downloaded {downloaded_at}. Source data is processed for this application."
    )
    if not metadata.attributions:
        return notice
    attributions = "; ".join(
        f"{row['organization_name']}: {row['attribution_url']}"
        for row in csv.DictReader(io.StringIO(metadata.attributions))
        if row.get("organization_name") and row.get("attribution_url")
    )
    return f"{notice} Attributions: {attributions}."


def _vehicle_notice(observed_at: datetime | None) -> str:
    stamp = observed_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if observed_at else "unknown"
    return (
        "Live vehicle positions: Miasto Stołeczne Warszawa, distributed in Warsaw GTFS-RT. "
        f"Feed timestamp: {stamp}. Source: https://api.um.warszawa.pl. "
        "Source data is processed for this application."
    )
