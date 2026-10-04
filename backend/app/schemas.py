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


class AffectedRoute(BaseModel):
    id: str
    label: str
    mode: str
    affected_stops: int


class SimulationResponse(BaseModel):
    id: str
    target_type: str
    target_id: str
    disruption_type: str
    duration_minutes: int
    affected_routes: list[AffectedRoute]
    affected_stops: list[str]
    impact_score: int
    data_mode: Literal["live", "demo"]
    notice: str
    created_at: datetime
