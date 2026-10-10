"""Read-only network endpoints and explainable disruption simulations."""

import csv
import gzip
import io
import json
import math
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .data_sources import LiveVehicleSnapshot, get_live_vehicles
from .database import get_db
from .delays import (
    EARLY_LIMIT_S,
    LATE_LIMIT_S,
    VehicleDelay,
    delays_for_snapshot,
    demo_line_delays,
)
from .models import (
    DelaySnapshot,
    FeedMetadata,
    Route,
    RouteSegment,
    RouteStop,
    SimulationRecord,
    Stop,
    VehiclePosition,
)
from .routing import Detour as RoutingDetour
from .routing import (
    Disruption,
    GraphRoute,
    GraphStop,
    Segment,
    TransitGraph,
    analyse_disruption,
    distance_meters,
)
from .schemas import (
    AffectedRoute,
    AlternativeRoute,
    AlternativeStop,
    Feature,
    FeatureCollection,
    HealthResponse,
    JourneyLeg,
    LinePunctuality,
    PunctualityPoint,
    PunctualityResponse,
    RouteLabel,
    RoutingSummary,
    SimulationRequest,
    SimulationResponse,
)
from .schemas import Detour as DetourResponse

router = APIRouter(prefix="/api")
FEED_ID = "warsaw-static-gtfs"
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
    features.extend(
        _feature(
            stop.id,
            {"type": "Point", "coordinates": [stop.longitude, stop.latitude]},
            {"entity_type": "stop", "name": stop.name},
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


@router.get("/vehicles", response_model=FeatureCollection, tags=["vehicles"])
def vehicles(db: Session = Depends(get_db)) -> FeatureCollection:
    live_snapshot = get_live_vehicles(db)
    if live_snapshot.vehicles:
        delays = {item.vehicle_id: item.delay_seconds for item in _safe_delays(db, live_snapshot)}
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
                        "delay_seconds": delays.get(vehicle.id),
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


@router.get("/punctuality", response_model=PunctualityResponse, tags=["vehicles"])
def punctuality(
    minutes: int = Query(default=120, ge=10, le=24 * 60),
    db: Session = Depends(get_db),
) -> PunctualityResponse:
    """Per-line delay history estimated from live positions against the timetable."""
    now = datetime.now(UTC)
    has_schedule = db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is not None
    if has_schedule:
        _safe_delays(db, get_live_vehicles(db))
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


def _safe_delays(db: Session, snapshot: LiveVehicleSnapshot) -> list[VehicleDelay]:
    try:
        return delays_for_snapshot(db, snapshot)
    except SQLAlchemyError:
        db.rollback()
        return []


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
        is_demo = stop.is_demo
        graph = load_graph(db, is_demo)
        closed_stop_ids = platform_group(graph, stop.id)
        disruption = Disruption(
            request.disruption_type,
            stop_ids=frozenset(closed_stop_ids),
            slowdown_factor=request.slowdown_factor,
        )
        routes = db.scalars(
            select(Route)
            .join(RouteStop, RouteStop.route_id == Route.id)
            .where(RouteStop.stop_id.in_(closed_stop_ids), Route.is_demo.is_(is_demo))
            .distinct()
            .order_by(Route.short_name)
        ).all()
        affected_stops = [stop.name]
        affected_stop_count = len(closed_stop_ids)
        alternative_stops = _nearby_stop_suggestions(db, stop)
    else:
        route = db.get(Route, request.target_id)
        if route is None:
            raise HTTPException(status_code=404, detail="Route not found")
        is_demo = route.is_demo
        graph = load_graph(db, is_demo)
        closed_stop_ids = []
        disruption = Disruption(
            request.disruption_type,
            route_ids=frozenset([route.id]),
            slowdown_factor=request.slowdown_factor,
        )
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

    impact = analyse_disruption(graph, disruption, request.duration_minutes)
    affected_routes = [
        AffectedRoute(
            id=route.id,
            label=route.short_name,
            mode=route.mode,
            affected_stops=affected_stop_count,
            affected_trips=impact.trips_by_route.get(route.id, 0),
        )
        for route in routes
    ]
    routing = RoutingSummary(
        affected_trips=impact.affected_trips,
        affected_segments=impact.affected_segments,
        sampled_journeys=len(impact.detours),
        average_added_minutes=impact.average_added_minutes,
        max_added_minutes=impact.max_added_minutes,
        unreachable_journeys=impact.unreachable_pairs,
        slowdown_factor=request.slowdown_factor if request.disruption_type == "slowdown" else None,
        closed_stop_ids=closed_stop_ids,
    )
    detours = [_detour_response(graph, detour) for detour in impact.detours[:8]]
    impact_score = sum(route.affected_stops for route in affected_routes) * request.duration_minutes
    created_at = datetime.now(UTC)
    result_payload = {
        "affected_routes": [route.model_dump() for route in affected_routes],
        "affected_stops": affected_stops,
        "affected_stop_count": affected_stop_count,
        "alternative_routes": [route.model_dump() for route in alternative_routes],
        "alternative_stops": [stop.model_dump() for stop in alternative_stops],
        "routing": routing.model_dump(),
        "detours": [detour.model_dump() for detour in detours],
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
        routing=routing,
        detours=detours,
        affected_stop_count=affected_stop_count,
        impact_score=impact_score,
        data_mode="demo" if is_demo else "live",
        notice=(
            "Timetable-based estimate. A closure removes the disrupted stop or line from the "
            "stop graph, a slowdown stretches its scheduled run times; detours are shortest "
            "paths with walking and transfer penalties, not live passenger counts or "
            "official forecasts."
        ),
        created_at=created_at,
    )


_graph_cache: dict[bool, tuple[object, TransitGraph]] = {}
_graph_lock = threading.Lock()


def load_graph(db: Session, is_demo: bool) -> TransitGraph:
    """Stop graph of the demo network or the imported timetable, rebuilt after an import."""
    version = _network_version(db, is_demo)
    cached = _graph_cache.get(is_demo)
    if cached and cached[0] == version:
        return cached[1]
    with _graph_lock:
        cached = _graph_cache.get(is_demo)
        if cached and cached[0] == version:
            return cached[1]
        graph = build_graph(db, is_demo)
        _graph_cache[is_demo] = (version, graph)
        return graph


def _network_version(db: Session, is_demo: bool) -> object:
    if is_demo:
        return "demo"  # seeded once at startup, before any request
    return db.scalar(select(FeedMetadata.downloaded_at).where(FeedMetadata.id == FEED_ID))


def build_graph(db: Session, is_demo: bool) -> TransitGraph:
    """Build the stop graph of either the demo network or the imported timetable."""
    stops = db.execute(
        select(Stop.id, Stop.name, Stop.latitude, Stop.longitude).where(Stop.is_demo.is_(is_demo))
    ).all()
    routes = db.execute(
        select(Route.id, Route.short_name, Route.mode).where(Route.is_demo.is_(is_demo))
    ).all()
    segments = db.execute(
        select(
            RouteSegment.route_id,
            RouteSegment.from_stop_id,
            RouteSegment.to_stop_id,
            RouteSegment.run_seconds,
            RouteSegment.trips,
            RouteSegment.service_minutes,
        )
        .join(Route, Route.id == RouteSegment.route_id)
        .where(Route.is_demo.is_(is_demo))
    ).all()
    return TransitGraph(
        (GraphStop(*row) for row in stops),
        (GraphRoute(*row) for row in routes),
        (Segment(*row) for row in segments),
    )


def platform_group(graph: TransitGraph, stop_id: str, radius_m: float = 400) -> list[str]:
    """All platforms that share the stop's name nearby: closing a stop closes all of them."""
    target = graph.stops.get(stop_id)
    if target is None:
        return [stop_id]
    return sorted(
        stop.id
        for stop in graph.stops.values()
        if stop.name == target.name
        and distance_meters(target.latitude, target.longitude, stop.latitude, stop.longitude)
        <= radius_m
    )


def _detour_response(graph: TransitGraph, detour: RoutingDetour) -> DetourResponse:
    routes = [graph.routes[route_id] for route_id in detour.route_ids]
    journey = detour.disrupted
    legs = []
    for leg in journey.legs if journey else ():
        leg_route = graph.routes.get(leg.route_id)
        legs.append(
            JourneyLeg(
                kind=leg.kind,
                line=leg_route.label if leg_route else None,
                mode=leg_route.mode if leg_route else None,
                from_stop=graph.stops[leg.from_stop].name,
                to_stop=graph.stops[leg.to_stop].name,
                minutes=round(leg.seconds / 60, 1),
                wait_minutes=round(leg.wait_seconds / 60, 1),
                stops=leg.stops,
            )
        )
    added = detour.added_seconds
    return DetourResponse(
        lines=[RouteLabel(id=route.id, label=route.label, mode=route.mode) for route in routes],
        origin=graph.stops[detour.origin].name,
        destination=graph.stops[detour.destination].name,
        baseline_minutes=round(detour.baseline.seconds / 60, 1),
        disrupted_minutes=round(journey.seconds / 60, 1) if journey else None,
        added_minutes=round(added / 60, 1) if added is not None else None,
        legs=legs,
    )


def _nearby_stop_suggestions(db: Session, target: Stop) -> list[AlternativeStop]:
    latitude_margin = 500 / 111_000
    longitude_margin = latitude_margin / max(math.cos(math.radians(target.latitude)), 0.1)
    candidates = db.scalars(
        select(Stop).where(
            Stop.is_demo.is_(target.is_demo),
            Stop.id != target.id,
            Stop.name != target.name,
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
    return distance_meters(lat1, lon1, lat2, lon2)


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
