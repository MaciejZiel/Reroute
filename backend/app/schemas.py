"""Stable API response and request shapes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Feature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: dict
    properties: dict


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature]
    data_mode: Literal["live", "demo"]
    observed_at: datetime | None
    notice: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]
    data_mode: Literal["live", "demo"]


class SimulationRequest(BaseModel):
    target_type: Literal["stop", "route"]
    target_id: str = Field(min_length=1, max_length=80)
    disruption_type: Literal["closure", "slowdown"]
    duration_minutes: int = Field(default=15, ge=5, le=120)
    slowdown_factor: float = Field(
        default=1.5,
        ge=1.1,
        le=4.0,
        description="Multiplier for scheduled run times of slowed segments (slowdown only).",
    )


class AffectedRoute(BaseModel):
    id: str
    label: str
    mode: str
    affected_stops: int
    affected_trips: int = 0


class AlternativeRoute(BaseModel):
    id: str
    label: str
    mode: str
    shared_stops: int


class AlternativeStop(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    distance_m: int
    routes: list[str]


class JourneyLeg(BaseModel):
    kind: Literal["ride", "walk"]
    line: str | None
    mode: str | None
    from_stop: str
    to_stop: str
    minutes: float
    wait_minutes: float
    stops: int


class RouteLabel(BaseModel):
    id: str
    label: str
    mode: str


class Detour(BaseModel):
    lines: list[RouteLabel]
    origin: str
    destination: str
    baseline_minutes: float
    disrupted_minutes: float | None
    added_minutes: float | None
    legs: list[JourneyLeg]


class RoutingSummary(BaseModel):
    affected_trips: int
    affected_segments: int
    sampled_journeys: int
    average_added_minutes: float | None
    max_added_minutes: float | None
    unreachable_journeys: int
    slowdown_factor: float | None
    closed_stop_ids: list[str]


class SimulationResponse(BaseModel):
    id: str
    target_type: str
    target_id: str
    disruption_type: str
    duration_minutes: int
    affected_routes: list[AffectedRoute]
    affected_stops: list[str]
    alternative_routes: list[AlternativeRoute]
    alternative_stops: list[AlternativeStop]
    routing: RoutingSummary
    detours: list[Detour]
    affected_stop_count: int
    impact_score: int
    data_mode: Literal["live", "demo"]
    notice: str
    created_at: datetime


class PunctualityPoint(BaseModel):
    observed_at: datetime
    vehicles: int
    mean_delay_minutes: float
    on_time_share: float


class LinePunctuality(BaseModel):
    line: str
    mode: str
    observations: int
    mean_delay_minutes: float
    median_delay_minutes: float
    early_share: float
    on_time_share: float
    late_share: float
    series: list[PunctualityPoint]


class PunctualityResponse(BaseModel):
    data_mode: Literal["live", "demo"]
    window_minutes: int
    snapshots: int
    observed_from: datetime | None
    observed_to: datetime | None
    on_time_definition: str
    lines: list[LinePunctuality]
    notice: str


class StopSummary(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    platform: str | None = Field(default=None, description="Platform number, when the id has one.")


class StopMatch(BaseModel):
    """A station found by name search: all platforms of one named stop."""

    id: str = Field(description="Id of the first platform; usable with /stops/{id}/departures.")
    name: str
    latitude: float
    longitude: float
    stop_ids: list[str]
    lines: list[str]


class StopSearchResponse(BaseModel):
    query: str
    results: list[StopMatch]


class Departure(BaseModel):
    trip_id: str
    stop_id: str
    platform: str | None
    headsign: str
    scheduled_at: datetime
    predicted_at: datetime | None = Field(
        description="Scheduled time plus the live delay; null without a live vehicle."
    )
    delay_seconds: int | None
    minutes: int = Field(description="Whole minutes until the expected time, never negative.")
    live: bool
    vehicle_id: str | None
    basis: Literal["live_delay", "scheduled"]


class LineDepartures(BaseModel):
    line: str
    route_id: str
    mode: str
    long_name: str
    departures: list[Departure]


class StopDeparturesResponse(BaseModel):
    stop: StopSummary
    platforms: list[StopSummary]
    generated_at: datetime
    horizon_minutes: int
    data_mode: Literal["live", "demo"]
    live_vehicles: int
    lines: list[LineDepartures]
    notice: str


class LineStop(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    sequence: int


class AlertInfo(BaseModel):
    id: str
    effect: str
    header_pl: str | None
    header_en: str | None
    url: str | None


class LineResponse(BaseModel):
    line: str
    route_id: str
    long_name: str
    mode: str
    shape: dict | None = Field(
        description="GeoJSON geometry of the route (one line per direction)."
    )
    stops: list[LineStop]
    alerts: list[AlertInfo]
    data_mode: Literal["live", "demo"]


class NextStop(BaseModel):
    stop_id: str
    name: str
    sequence: int
    scheduled_at: datetime
    predicted_at: datetime | None
    delay_seconds: int | None


class ModelInfo(BaseModel):
    name: str
    version: str
    kind: Literal["baseline", "model"]


class VehicleDetail(BaseModel):
    vehicle_id: str
    label: str | None
    line: str
    mode: str
    trip_id: str | None
    headsign: str | None
    observed_at: datetime
    age_seconds: int
    latitude: float
    longitude: float
    delay_seconds: int | None
    status: Literal["ok", "layover", "off_route", "no_schedule", "stale"]
    next_stops: list[NextStop]
    model: ModelInfo


class PredictionItem(BaseModel):
    trip_id: str
    line: str
    mode: str
    stop_id: str
    stop_sequence: int
    scheduled_at: datetime
    predicted_at: datetime
    predicted_delay_s: int
    lower_delay_s: int | None
    upper_delay_s: int | None
    basis: Literal["live_delay", "scheduled"]


class PredictionsResponse(BaseModel):
    generated_at: datetime
    model: ModelInfo
    predictions: list[PredictionItem]
