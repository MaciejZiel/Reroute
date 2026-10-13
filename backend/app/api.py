"""Network, health and punctuality endpoints."""

import csv
import gzip
import io
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

from fastapi import APIRouter, Depends, Query, Request, Response
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
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


@router.get("/punctuality", response_model=PunctualityResponse, tags=["vehicles"])
def punctuality(
    minutes: int = Query(default=120, ge=10, le=24 * 60),
    db: Session = Depends(get_db),
) -> PunctualityResponse:
    """Per-line delay history estimated from live positions against the timetable."""
    now = datetime.now(UTC)
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
    if has_schedule:
        ensure_fresh(db)
        rows = db.scalars(
            select(DelaySnapshot)
            .where(DelaySnapshot.observed_at >= now - timedelta(minutes=minutes))
            .order_by(DelaySnapshot.observed_at)
        ).all()
        notice = (
            "Delays estimated by projecting live vehicle positions (Miasto Stołeczne Warszawa, "
            "GTFS-Realtime by Mikołaj Kuranowski) onto the ZTM GTFS timetable of the trip "
            "each vehicle reports. Snapshots are stored at most once a minute while the app "
            "is in use."
        )
    else:
        demo_lines = db.execute(
            select(Route.short_name, Route.mode)
            .where(Route.is_demo.is_(True))
            .order_by(Route.short_name)
        ).all()
        rows = [
            row
            for row in demo_line_delays(now, [tuple(line) for line in demo_lines])
            if row.observed_at >= now - timedelta(minutes=minutes)
        ]
        notice = (
            "Demo network: the punctuality history is fictional. Import the Warsaw timetable "
            "to estimate real delays from live positions."
        )
    return PunctualityResponse(
        data_mode="live" if has_schedule else "demo",
        window_minutes=minutes,
        snapshots=len({row.observed_at for row in rows}),
        observed_from=rows[0].observed_at if rows else None,
        observed_to=rows[-1].observed_at if rows else None,
        on_time_definition=(
            f"On time: between {-EARLY_LIMIT_S // 60} min early and {LATE_LIMIT_S // 60} min late."
        ),
        lines=_line_punctuality(rows),
        notice=notice,
    )


def _line_punctuality(rows: list[DelaySnapshot]) -> list[LinePunctuality]:
    by_line: dict[tuple[str, str], list[DelaySnapshot]] = defaultdict(list)
    for row in rows:
        by_line[(row.line, row.mode)].append(row)
    result = []
    for (line, mode), snapshots in by_line.items():
        observations = sum(row.vehicles for row in snapshots)
        if not observations:
            continue
        result.append(
            LinePunctuality(
                line=line,
                mode=mode,
                observations=observations,
                mean_delay_minutes=round(
                    sum(row.mean_delay_seconds * row.vehicles for row in snapshots)
                    / observations
                    / 60,
                    1,
                ),
                median_delay_minutes=round(
                    median(row.median_delay_seconds for row in snapshots) / 60, 1
                ),
                early_share=round(sum(row.early for row in snapshots) / observations, 3),
                on_time_share=round(sum(row.on_time for row in snapshots) / observations, 3),
                late_share=round(sum(row.late for row in snapshots) / observations, 3),
                series=[
                    PunctualityPoint(
                        observed_at=row.observed_at,
                        vehicles=row.vehicles,
                        mean_delay_minutes=round(row.mean_delay_seconds / 60, 1),
                        on_time_share=round(row.on_time / row.vehicles, 3) if row.vehicles else 0,
                    )
                    for row in snapshots
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
