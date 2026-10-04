"""Read-only network endpoints and explainable disruption simulations."""

import csv
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_sources import get_live_vehicles
from .database import get_db
from .models import FeedMetadata, Route, RouteStop, SimulationRecord, Stop, VehiclePosition
from .schemas import (
    AffectedRoute,
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
    else:
        route = db.get(Route, request.target_id)
        if route is None:
            raise HTTPException(status_code=404, detail="Route not found")
        routes = [route]
        affected_stops = list(
            db.scalars(
                select(Stop.name)
                .join(RouteStop, RouteStop.stop_id == Stop.id)
                .where(RouteStop.route_id == route.id, Stop.is_demo.is_(route.is_demo))
                .order_by(RouteStop.sequence)
            ).all()
        )

    if not routes:
        raise HTTPException(status_code=422, detail="No routes serve this stop")

    affected_routes = [
        AffectedRoute(
            id=route.id,
            label=route.short_name,
            mode=route.mode,
            affected_stops=1 if request.target_type == "stop" else len(affected_stops),
        )
        for route in routes
    ]
    impact_score = sum(route.affected_stops for route in affected_routes) * request.duration_minutes
    created_at = datetime.now(UTC)
    result_payload = {
        "affected_routes": [route.model_dump() for route in affected_routes],
        "affected_stops": affected_stops,
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
        impact_score=impact_score,
        data_mode="demo"
        if (stop.is_demo if request.target_type == "stop" else route.is_demo)
        else "live",
        notice=(
            "Illustrative impact score = affected route stops × disruption duration; "
            "not an official service forecast."
        ),
        created_at=created_at,
    )


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
