"""Read-only network endpoints and explainable disruption simulations."""

import csv
import io
import json
import math
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .data_sources import get_live_vehicles
from .database import get_db
from .models import FeedMetadata, Route, RouteStop, SimulationRecord, Stop, VehiclePosition
from .schemas import (
    AffectedRoute,
    AlternativeRoute,
    AlternativeStop,
    Feature,
    FeatureCollection,
    HealthResponse,
    SimulationRequest,
    SimulationResponse,
)

router = APIRouter(prefix="/api")
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


@router.get("/network", response_model=FeatureCollection, tags=["network"])
def network(db: Session = Depends(get_db)) -> FeatureCollection:
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
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
    features.extend(
        _feature(
            stop.id,
            {"type": "Point", "coordinates": [stop.longitude, stop.latitude]},
            {"entity_type": "stop", "name": stop.name},
        )
        for stop in stops
    )
    metadata = db.get(FeedMetadata, "warsaw-static-gtfs") if has_schedule else None
    notice = _schedule_notice(metadata) if has_schedule else DEMO_NOTICE
    return FeatureCollection(
        features=features,
        data_mode="live" if has_schedule else "demo",
        observed_at=None,
        notice=notice,
    )


@router.get("/vehicles", response_model=FeatureCollection, tags=["vehicles"])
def vehicles(db: Session = Depends(get_db)) -> FeatureCollection:
    live_snapshot = get_live_vehicles(db)
    if live_snapshot.vehicles:
        return FeatureCollection(
            features=[
                _feature(
                    vehicle.id,
                    {"type": "Point", "coordinates": [vehicle.longitude, vehicle.latitude]},
                    {
                        "entity_type": "vehicle",
                        "vehicle_number": vehicle.id,
                        "line": vehicle.line,
                        "mode": vehicle.mode,
                        "observed_at": vehicle.observed_at.isoformat(),
                        "is_stale": vehicle.is_stale,
                    },
                )
                for vehicle in live_snapshot.vehicles
            ],
            data_mode="live",
            observed_at=live_snapshot.observed_at,
            notice=_vehicle_notice(live_snapshot.observed_at),
        )

    vehicles = db.scalars(select(VehiclePosition).where(VehiclePosition.is_demo.is_(True))).all()
    newest = max((vehicle.observed_at for vehicle in vehicles), default=None)
    features = [
        _feature(
            f"{vehicle.vehicle_number}-{vehicle.id}",
            {"type": "Point", "coordinates": [vehicle.longitude, vehicle.latitude]},
            {
                "entity_type": "vehicle",
                "vehicle_number": vehicle.vehicle_number,
                "line": vehicle.line,
                "mode": vehicle.mode,
                "observed_at": vehicle.observed_at.isoformat(),
                "is_stale": False,
            },
        )
        for vehicle in vehicles
    ]
    return FeatureCollection(
        features=features,
        data_mode="demo",
        observed_at=newest,
        notice=(
            "Live vehicle positions are unavailable. Showing fictional demo vehicles."
            if live_snapshot.error != "not fetched"
            else DEMO_NOTICE
        ),
    )


@router.post("/simulations", response_model=SimulationResponse, tags=["simulations"])
def simulate(
    request: SimulationRequest,
    db: Session = Depends(get_db),
) -> SimulationResponse:
    alternative_routes: list[AlternativeRoute] = []
    alternative_stops: list[AlternativeStop] = []
    if request.target_type == "stop":
        stop = db.get(Stop, request.target_id)
        if stop is None:
            raise HTTPException(status_code=404, detail="Stop not found")
        routes = db.scalars(
            select(Route)
            .join(RouteStop, RouteStop.route_id == Route.id)
            .where(RouteStop.stop_id == stop.id, Route.is_demo.is_(stop.is_demo))
            .order_by(Route.short_name)
        ).all()
        affected_stops = [stop.name]
        affected_stop_count = 1
        alternative_stops = _nearby_stop_suggestions(db, stop)
    else:
        route = db.get(Route, request.target_id)
        if route is None:
            raise HTTPException(status_code=404, detail="Route not found")
        routes = [route]
        route_stops = db.execute(
            select(RouteStop.stop_id, Stop.name)
            .join(Stop, Stop.id == RouteStop.stop_id)
            .where(RouteStop.route_id == route.id, Stop.is_demo.is_(route.is_demo))
            .order_by(RouteStop.sequence)
        ).all()
        affected_stops = list(dict.fromkeys(name for _, name in route_stops))
        stop_ids = list({stop_id for stop_id, _ in route_stops})
        affected_stop_count = len(stop_ids)
        rows = db.execute(
            select(
                Route.id,
                Route.short_name,
                Route.mode,
                func.count(distinct(RouteStop.stop_id)).label("shared_stops"),
            )
            .join(RouteStop, RouteStop.route_id == Route.id)
            .where(
                RouteStop.stop_id.in_(stop_ids),
                Route.id != route.id,
                Route.is_demo.is_(route.is_demo),
            )
            .group_by(Route.id, Route.short_name, Route.mode)
            .order_by(func.count(distinct(RouteStop.stop_id)).desc(), Route.short_name)
            .limit(12)
        ).all()
        alternative_routes = [
            AlternativeRoute(
                id=route_id,
                label=label,
                mode=mode,
                shared_stops=shared_stops,
            )
            for route_id, label, mode, shared_stops in rows
        ]

    if not routes:
        raise HTTPException(status_code=422, detail="No routes serve this stop")

    affected_routes = [
        AffectedRoute(
            id=route.id,
            label=route.short_name,
            mode=route.mode,
            affected_stops=affected_stop_count,
        )
        for route in routes
    ]
    impact_score = sum(route.affected_stops for route in affected_routes) * request.duration_minutes
    created_at = datetime.now(UTC)
    result_payload = {
        "affected_routes": [route.model_dump() for route in affected_routes],
        "affected_stops": affected_stops,
        "affected_stop_count": affected_stop_count,
        "alternative_routes": [route.model_dump() for route in alternative_routes],
        "alternative_stops": [stop.model_dump() for stop in alternative_stops],
        "impact_score": impact_score,
    }
    record = SimulationRecord(
        id=str(uuid4()),
        target_type=request.target_type,
        target_id=request.target_id,
        disruption_type=request.disruption_type,
        duration_minutes=request.duration_minutes,
        result_json=json.dumps(result_payload),
        created_at=created_at,
    )
    db.add(record)
    db.commit()
    return SimulationResponse(
        id=record.id,
        target_type=record.target_type,
        target_id=record.target_id,
        disruption_type=record.disruption_type,
        duration_minutes=record.duration_minutes,
        affected_routes=affected_routes,
        affected_stops=affected_stops,
        alternative_routes=alternative_routes,
        alternative_stops=alternative_stops,
        affected_stop_count=affected_stop_count,
        impact_score=impact_score,
        data_mode="demo"
        if (stop.is_demo if request.target_type == "stop" else route.is_demo)
        else "live",
        notice=(
            "Static GTFS network analysis. Nearby stops and shared lines are suggestions, "
            "not passenger routing, live detour or travel-time predictions."
        ),
        created_at=created_at,
    )


def _nearby_stop_suggestions(db: Session, target: Stop) -> list[AlternativeStop]:
    latitude_margin = 500 / 111_000
    longitude_margin = latitude_margin / max(math.cos(math.radians(target.latitude)), 0.1)
    candidates = db.scalars(
        select(Stop).where(
            Stop.is_demo.is_(target.is_demo),
            Stop.id != target.id,
            Stop.latitude.between(
                target.latitude - latitude_margin, target.latitude + latitude_margin
            ),
            Stop.longitude.between(
                target.longitude - longitude_margin, target.longitude + longitude_margin
            ),
        )
    ).all()
    nearby = _rank_nearby_stops(target, candidates)
    if not nearby:
        return []

    routes_by_stop: dict[str, set[str]] = {stop.id: set() for stop, _ in nearby}
    route_rows = db.execute(
        select(RouteStop.stop_id, Route.short_name)
        .join(Route, Route.id == RouteStop.route_id)
        .where(
            RouteStop.stop_id.in_(routes_by_stop),
            Route.is_demo.is_(target.is_demo),
        )
    ).all()
    for stop_id, short_name in route_rows:
        routes_by_stop[stop_id].add(short_name)

    grouped: dict[str, list[tuple[Stop, int]]] = {}
    for stop, distance in nearby:
        if routes_by_stop[stop.id]:
            grouped.setdefault(stop.name, []).append((stop, distance))

    suggestions = []
    for name, platforms in grouped.items():
        nearest, distance = platforms[0]
        line_names = {line for stop, _ in platforms for line in routes_by_stop[stop.id]}
        suggestions.append(
            AlternativeStop(
                id=nearest.id,
                name=name,
                latitude=nearest.latitude,
                longitude=nearest.longitude,
                distance_m=distance,
                routes=sorted(line_names),
            )
        )
    return suggestions[:5]


def _rank_nearby_stops(
    target: Stop, candidates: list[Stop], limit: int = 12
) -> list[tuple[Stop, int]]:
    suggestions = []
    for candidate in candidates:
        distance = _distance_meters(
            target.latitude, target.longitude, candidate.latitude, candidate.longitude
        )
        if 0 < distance <= 500:
            suggestions.append((candidate, round(distance)))
    return sorted(suggestions, key=lambda item: item[1])[:limit]


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(haversine))


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
