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
