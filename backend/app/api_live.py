"""Endpoints of the live map: stop search, departures, lines, vehicles and predictions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .api import DEMO_NOTICE, _vehicle_notice
from .database import get_db
from .departures import Candidate, by_route, trip_stops, upcoming
from .features import Target, live_feature_rows
from .live import LiveVehicle, fallback_error, snapshot, vehicle_by_id, vehicles_of_trips
from .models import Route, RouteStop, Stop, TripSchedule, VehiclePosition
from .predict import Prediction, get_predictor
from .schemas import (
    AlertInfo,
    Departure,
    Factor,
    Feature,
    FeatureCollection,
    LineDepartures,
    LineResponse,
    LineStop,
    ModelInfo,
    NextStop,
    PredictionItem,
    PredictionsResponse,
    StopDeparturesResponse,
    StopMatch,
    StopSearchResponse,
    StopSummary,
    VehicleDetail,
    Why,
)
from .stops import StopInfo, centroid, lines_at, natural_key, stop_index

router = APIRouter(prefix="/api")
DbSession = Annotated[Session, Depends(get_db)]
LIVE_NOTICE = (
    "Departures come from the ZTM GTFS timetable; delays are estimated by projecting live "
    "vehicle positions (Miasto Stołeczne Warszawa, GTFS-Realtime by Mikołaj Kuranowski) onto "
    "the timetable of the trip each vehicle runs. They are estimates, not an official forecast."
)
DEMO_TIMETABLE_NOTICE = "Demo network: there is no timetable, so no departures are listed."
REIMPORT_DETAIL = (
    "The imported timetable predates trip dates. Run `python -m app.import_gtfs` again."
)


def _is_demo(db: Session) -> bool:
    return db.scalar(select(Route.id).where(Route.is_demo.is_(False)).limit(1)) is None


def _require_timetable(db: Session, is_demo: bool) -> None:
    if is_demo:
        return
    has_dates = db.scalar(
        select(TripSchedule.trip_id).where(TripSchedule.service_date.is_not(None)).limit(1)
    )
    if has_dates is None:
        raise HTTPException(status_code=503, detail=REIMPORT_DETAIL)


def _summary(stop: StopInfo) -> StopSummary:
    return StopSummary(
        id=stop.id,
        name=stop.name,
        latitude=stop.latitude,
        longitude=stop.longitude,
        platform=stop.platform,
    )


def _now(at: datetime | None) -> datetime:
    return at.astimezone(UTC) if at else datetime.now(UTC)


@router.get("/stops", response_model=StopSearchResponse, tags=["stops"])
def search_stops(
    db: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=80, description="Part of a stop name.")],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> StopSearchResponse:
    """Stations by name, ignoring case and Polish diacritics; prefix matches first."""
    is_demo = _is_demo(db)
    stations = stop_index(db, is_demo).search(q, limit)
    ids = [stop.id for station in stations for stop in station]
    lines: dict[str, set[str]] = {}
    if ids:
        rows = db.execute(
            select(RouteStop.stop_id, Route.short_name)
            .join(Route, Route.id == RouteStop.route_id)
            .where(RouteStop.stop_id.in_(ids), Route.is_demo.is_(is_demo))
        ).all()
        for stop_id, short_name in rows:
            lines.setdefault(stop_id, set()).add(short_name)
    results = []
    for station in stations:
        latitude, longitude = centroid(station)
        names = {name for stop in station for name in lines.get(stop.id, ())}
        results.append(
            StopMatch(
                id=station[0].id,
                name=station[0].name,
                latitude=latitude,
                longitude=longitude,
                stop_ids=[stop.id for stop in station],
                lines=sorted(names, key=natural_key),
            )
        )
    return StopSearchResponse(query=q, results=results)


@router.get("/stops/{stop_id}/departures", response_model=StopDeparturesResponse, tags=["stops"])
def stop_departures(
    stop_id: str,
    db: DbSession,
    scope: Annotated[str, Query(pattern="^(station|platform)$")] = "station",
    per_line: Annotated[int, Query(ge=1, le=10)] = 4,
    horizon_minutes: Annotated[int, Query(ge=10, le=360)] = 120,
    at: Annotated[datetime | None, Query(description="Override the clock (testing).")] = None,
) -> StopDeparturesResponse:
    """Every line serving the stop, each with its next departures and live delay if matched."""
    is_demo = _is_demo(db)
    index = stop_index(db, is_demo)
    stop = index.by_id.get(stop_id)
    if stop is None:
        raise HTTPException(status_code=404, detail="Stop not found")
    _require_timetable(db, is_demo)
    now = _now(at)
    platforms = index.station(stop) if scope == "station" else [stop]
    lines = lines_at(db, [platform.id for platform in platforms], is_demo)
    names = {route_id: short for route_id, short, _, _ in lines}
    horizon = timedelta(minutes=horizon_minutes)
    grouped = by_route(upcoming(db, platforms, now, horizon, names)) if not is_demo else {}
    platform_of = {platform.id: platform.platform for platform in platforms}
    result = []
    for route_id, short_name, long_name, mode in lines:
        departures = [
            _departure(candidate, platform_of, now) for candidate in grouped.get(route_id, [])
        ][:per_line]
        result.append(
            LineDepartures(
                line=short_name,
                route_id=route_id,
                mode=mode,
                long_name=long_name,
                departures=departures,
            )
        )
    result.sort(
        key=lambda item: (
            not item.departures,
            item.departures[0].predicted_at or item.departures[0].scheduled_at
            if item.departures
            else now,
            natural_key(item.line),
        )
    )
    return StopDeparturesResponse(
        stop=_summary(stop),
        platforms=[_summary(platform) for platform in platforms],
        generated_at=now,
        horizon_minutes=horizon_minutes,
        data_mode="demo" if is_demo else "live",
        live_vehicles=sum(1 for item in result for d in item.departures if d.live),
        lines=result,
        notice=DEMO_TIMETABLE_NOTICE if is_demo else LIVE_NOTICE,
    )


def _departure(
    candidate: Candidate, platform_of: dict[str, str | None], now: datetime
) -> Departure:
    live = candidate.prediction is not None and candidate.prediction.basis == "live_delay"
    expected = candidate.expected_at
    delay = candidate.prediction.predicted_delay_s if live and candidate.prediction else None
    return Departure(
        trip_id=candidate.trip_id,
        stop_id=candidate.stop_id,
        platform=platform_of.get(candidate.stop_id),
        headsign=candidate.headsign,
        scheduled_at=candidate.scheduled_at,
        predicted_at=expected if live else None,
        delay_seconds=delay,
        minutes=max(int((expected - now).total_seconds() // 60), 0),
        live=live,
        vehicle_id=candidate.vehicle.vehicle_id if candidate.vehicle else None,
        basis="live_delay" if live else "scheduled",
    )


def _resolve_route(db: Session, line: str) -> Route:
    route = db.scalars(
        select(Route)
        .where((Route.id == line) | (Route.short_name == line))
        .order_by(Route.is_demo, (Route.id != line))
        .limit(1)
    ).first()
    if route is None:
        raise HTTPException(status_code=404, detail="Line not found")
    return route


@router.get("/lines/{line}", response_model=LineResponse, tags=["lines"])
def line_detail(line: str, db: DbSession) -> LineResponse:
    """Route shape, stops and active alerts of one line (by number or route id)."""
    route = _resolve_route(db, line)
    stops = db.execute(
        select(Stop.id, Stop.name, Stop.latitude, Stop.longitude, RouteStop.sequence)
        .join(RouteStop, RouteStop.stop_id == Stop.id)
        .where(RouteStop.route_id == route.id)
        .order_by(RouteStop.sequence)
    ).all()
    now = datetime.now(UTC)
    alerts = db.execute(
        text(
            "SELECT DISTINCT a.alert_id, a.effect, a.header_pl, a.header_en, a.url "
            "FROM alerts a JOIN alert_entities e ON e.alert_id = a.alert_id "
            "WHERE e.route_id = :route AND a.last_seen >= :since "
            "AND (a.active_from IS NULL OR a.active_from <= :now) "
            "AND (a.active_to IS NULL OR a.active_to >= :now)"
        ),
        {"route": route.id, "since": now - timedelta(minutes=5), "now": now},
    ).all()
    return LineResponse(
        line=route.short_name,
        route_id=route.id,
        long_name=route.long_name,
        mode=route.mode,
        shape=mapping(to_shape(route.shape)) if route.shape is not None else None,
        stops=[
            LineStop(id=i, name=n, latitude=la, longitude=lo, sequence=s)
            for i, n, la, lo, s in stops
        ],
        alerts=[
            AlertInfo(id=i, effect=e, header_pl=hp, header_en=he, url=u)
            for i, e, hp, he, u in alerts
        ],
        data_mode="demo" if route.is_demo else "live",
    )


def _vehicle_collection(
    vehicles: tuple[LiveVehicle, ...], observed_at: datetime | None
) -> FeatureCollection:
    return FeatureCollection(
        features=[
            Feature(
                id=vehicle.vehicle_id,
                geometry={"type": "Point", "coordinates": [vehicle.longitude, vehicle.latitude]},
                properties={
                    "entity_type": "vehicle",
                    "vehicle_number": vehicle.vehicle_id,
                    "label": vehicle.label,
                    "line": vehicle.line,
                    "mode": vehicle.mode,
                    "trip_id": vehicle.trip_id,
                    "bearing": vehicle.bearing,
                    "observed_at": vehicle.observed_at.isoformat(),
                    "is_stale": vehicle.is_stale,
                    "delay_seconds": vehicle.delay_s if vehicle.has_delay else None,
                },
            )
            for vehicle in vehicles
        ],
        data_mode="live",
        observed_at=observed_at,
        notice=_vehicle_notice(observed_at),
    )


@router.get("/vehicles", response_model=FeatureCollection, tags=["vehicles"])
def vehicles(db: DbSession) -> FeatureCollection:
    """All live vehicles; clearly labelled demo vehicles when the feed is unavailable."""
    live = snapshot(db)
    if live.vehicles:
        return _vehicle_collection(live.vehicles, live.observed_at)
    demo = db.scalars(select(VehiclePosition).where(VehiclePosition.is_demo.is_(True))).all()
    return FeatureCollection(
        features=[
            Feature(
                id=f"{vehicle.vehicle_number}-{vehicle.id}",
                geometry={"type": "Point", "coordinates": [vehicle.longitude, vehicle.latitude]},
                properties={
                    "entity_type": "vehicle",
                    "vehicle_number": vehicle.vehicle_number,
                    "line": vehicle.line,
                    "mode": vehicle.mode,
                    "observed_at": vehicle.observed_at.isoformat(),
                    "is_stale": False,
                },
            )
            for vehicle in demo
        ],
        data_mode="demo",
        observed_at=max((vehicle.observed_at for vehicle in demo), default=None),
        notice=(
            f"Live vehicle positions are unavailable ({fallback_error()}). "
            "Showing fictional demo vehicles."
            if fallback_error()
            else DEMO_NOTICE
        ),
    )


@router.get("/lines/{line}/vehicles", response_model=FeatureCollection, tags=["lines"])
def line_vehicles(line: str, db: DbSession) -> FeatureCollection:
    """Live vehicles of one line only, with their delay against the timetable."""
    route = _resolve_route(db, line)
    live = snapshot(db, route.short_name)
    return _vehicle_collection(live.vehicles, live.observed_at)


def _status(vehicle: LiveVehicle) -> str:
    if vehicle.is_stale:
        return "stale"
    return {0: "ok", 4: "layover", 3: "off_route"}.get(vehicle.match_status, "no_schedule")


@router.get("/vehicles/{vehicle_id:path}", response_model=VehicleDetail, tags=["vehicles"])
def vehicle_detail(vehicle_id: str, db: DbSession) -> VehicleDetail:
    """One vehicle: delay against the schedule and its next stops with expected times."""
    vehicle = vehicle_by_id(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    now = datetime.now(UTC)
    predictor = get_predictor()
    headsign = None
    next_stops: list[NextStop] = []
    if vehicle.trip_id:
        trip = trip_stops(db, vehicle.trip_id)
        if trip is not None:
            headsign = trip.headsign or None
            first = max((vehicle.next_stop_seq or 1) - 1, 0)
            upcoming_stops = trip.stops[first : first + 8]
            targets = [
                Target(
                    trip_id=trip.trip_id,
                    line=vehicle.line,
                    route_id=trip.route_id,
                    stop_id=stop.stop_id,
                    stop_seq=stop.position + 1,
                    scheduled_at=stop.scheduled_at,
                    service_date=trip.service_date,
                    vehicle_id=vehicle.vehicle_id,
                    current_delay_s=vehicle.delay_s if vehicle.has_delay else None,
                    next_stop_seq=vehicle.next_stop_seq,
                )
                for stop in upcoming_stops
            ]
            rows = live_feature_rows(
                db,
                targets,
                now,
                with_history=predictor.kind != "baseline",
                with_model_inputs=predictor.kind == "model",
            )
            for stop, prediction in zip(upcoming_stops, predictor.predict(rows), strict=True):
                live = prediction.basis == "live_delay"
                next_stops.append(
                    NextStop(
                        stop_id=stop.stop_id,
                        name=stop.name,
                        sequence=stop.position + 1,
                        scheduled_at=stop.scheduled_at,
                        predicted_at=(
                            stop.scheduled_at + timedelta(seconds=prediction.predicted_delay_s)
                            if live
                            else None
                        ),
                        delay_seconds=prediction.predicted_delay_s if live else None,
                        lower_delay_seconds=prediction.lower_delay_s if live else None,
                        upper_delay_seconds=prediction.upper_delay_s if live else None,
                        why=_why(prediction),
                    )
                )
    return VehicleDetail(
        vehicle_id=vehicle.vehicle_id,
        label=vehicle.label,
        line=vehicle.line,
        mode=vehicle.mode,
        trip_id=vehicle.trip_id,
        headsign=headsign,
        observed_at=vehicle.observed_at,
        age_seconds=max(int((now - vehicle.observed_at).total_seconds()), 0),
        latitude=vehicle.latitude,
        longitude=vehicle.longitude,
        delay_seconds=vehicle.delay_s if vehicle.has_delay else None,
        status=_status(vehicle),
        next_stops=next_stops,
        model=ModelInfo(name=predictor.name, version=predictor.version, kind=predictor.kind),
    )


@router.get("/predictions", response_model=PredictionsResponse, tags=["predictions"])
def predictions(
    db: DbSession,
    stop_id: str | None = None,
    trip_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> PredictionsResponse:
    """Predicted arrival delays at a stop (next departures) or along a trip.

    The model behind it is replaceable (see ``predict.py``); ``model`` says which one answered.
    """
    if (stop_id is None) == (trip_id is None):
        raise HTTPException(status_code=422, detail="Pass exactly one of stop_id or trip_id")
    now = datetime.now(UTC)
    predictor = get_predictor()
    items: list[PredictionItem] = []
    if stop_id is not None:
        is_demo = _is_demo(db)
        index = stop_index(db, is_demo)
        stop = index.by_id.get(stop_id)
        if stop is None:
            raise HTTPException(status_code=404, detail="Stop not found")
        _require_timetable(db, is_demo)
        names = {route_id: short for route_id, short, _, _ in lines_at(db, [stop.id], is_demo)}
        modes = {route_id: mode for route_id, _, _, mode in lines_at(db, [stop.id], is_demo)}
        candidates = upcoming(db, [stop], now, line_names=names)
        candidates.sort(key=lambda item: item.expected_at)
        for candidate in candidates[:limit]:
            prediction = candidate.prediction or Prediction(0)
            items.append(
                _item(
                    candidate.trip_id,
                    names.get(candidate.route_id, candidate.route_id),
                    modes.get(candidate.route_id, "bus"),
                    candidate.stop_id,
                    candidate.position + 1,
                    candidate.scheduled_at,
                    prediction,
                )
            )
    else:
        assert trip_id is not None
        trip = trip_stops(db, trip_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        vehicles = vehicles_of_trips(db, [trip_id]).get(trip_id)
        route = db.get(Route, trip.route_id)
        first = max((vehicles.next_stop_seq or 1) - 1, 0) if vehicles else 0
        stops = trip.stops[first : first + limit]
        targets = [
            Target(
                trip_id=trip.trip_id,
                line=route.short_name if route else trip.route_id,
                route_id=trip.route_id,
                stop_id=stop.stop_id,
                stop_seq=stop.position + 1,
                scheduled_at=stop.scheduled_at,
                service_date=trip.service_date,
                vehicle_id=vehicles.vehicle_id if vehicles else None,
                current_delay_s=vehicles.delay_s if vehicles and vehicles.has_delay else None,
                next_stop_seq=vehicles.next_stop_seq if vehicles else None,
            )
            for stop in stops
        ]
        rows = live_feature_rows(
            db, targets, now, with_history=True, with_model_inputs=predictor.kind == "model"
        )
        for stop, prediction in zip(stops, predictor.predict(rows), strict=True):
            items.append(
                _item(
                    trip.trip_id,
                    route.short_name if route else trip.route_id,
                    route.mode if route else "bus",
                    stop.stop_id,
                    stop.position + 1,
                    stop.scheduled_at,
                    prediction,
                )
            )
    return PredictionsResponse(
        generated_at=now,
        model=ModelInfo(name=predictor.name, version=predictor.version, kind=predictor.kind),
        predictions=items,
    )


def _item(
    trip_id: str,
    line: str,
    mode: str,
    stop_id: str,
    stop_sequence: int,
    scheduled_at: datetime,
    prediction: Prediction,
) -> PredictionItem:
    return PredictionItem(
        trip_id=trip_id,
        line=line,
        mode=mode,
        stop_id=stop_id,
        stop_sequence=stop_sequence,
        scheduled_at=scheduled_at,
        predicted_at=scheduled_at + timedelta(seconds=prediction.predicted_delay_s),
        predicted_delay_s=prediction.predicted_delay_s,
        lower_delay_s=prediction.lower_delay_s,
        upper_delay_s=prediction.upper_delay_s,
        basis=prediction.basis,  # type: ignore[arg-type]
        why=_why(prediction),
    )


def _why(prediction: Prediction) -> Why | None:
    if prediction.explanation is None:
        return None
    return Why(
        summary=prediction.explanation.summary,
        factors=[
            Factor(feature=f.feature, effect_seconds=f.effect_s, text=f.text)
            for f in prediction.explanation.factors
        ],
    )
