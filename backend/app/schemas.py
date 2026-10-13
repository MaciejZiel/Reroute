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


class Factor(BaseModel):
    feature: str
    effect_seconds: int = Field(description="Positive: later than the current delay alone implies")
    text: str


class Why(BaseModel):
    summary: str
    factors: list[Factor]


class NextStop(BaseModel):
    stop_id: str
    name: str
    sequence: int
    scheduled_at: datetime
    predicted_at: datetime | None
    delay_seconds: int | None
    lower_delay_seconds: int | None = None
    upper_delay_seconds: int | None = None
    why: Why | None = None
    model_status: Literal["model", "held", "paused", "out_of_range", "passed"] | None = Field(
        default=None, description="Only with a trained model; see app.predict.Prediction"
    )
    pause_reason: Literal["no_vehicle", "vehicle_report", "last_stop", "no_stop"] | None = None


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
    why: Why | None = None
    model_status: Literal["model", "held", "paused", "out_of_range", "passed"] | None = None
    pause_reason: Literal["no_vehicle", "vehicle_report", "last_stop", "no_stop"] | None = None


class PredictionsResponse(BaseModel):
    generated_at: datetime
    model: ModelInfo
    predictions: list[PredictionItem]


class CardInterval(BaseModel):
    value: float
    low: float
    high: float


class CardScore(BaseModel):
    name: str
    mae_s: CardInterval
    rmse_s: CardInterval
    bias_s: float
    mae_by_horizon_s: dict[str, float]


class CardCoverage(BaseModel):
    label: str
    predictions: int
    coverage: CardInterval
    held_out: bool
    window: str


class ModelCardResponse(BaseModel):
    """What the served model is and how it was checked, read from the model file's card. Nothing
    here is computed per request and nothing is retrained: it is the record of the one training
    run (and of the checks run on its stored file)."""

    status: Literal["model", "baseline"]
    version: str | None = None
    kind: str | None = None
    promoted: bool | None = None
    trained_at: str | None = None
    training_window: dict[str, str] | None = None
    test_window: dict[str, str] | None = None
    data: dict | None = None
    sample_counts: dict[str, int] | None = None
    best_baseline: str | None = None
    gain_vs_best_baseline: CardInterval | None = None
    gain_vs_ridge: CardInterval | None = None
    scores: list[CardScore] = Field(default_factory=list)
    interval_nominal: float | None = None
    interval_mean_width_s: float | None = None
    calibration_by_horizon: list[CardCoverage] = Field(default_factory=list)
    calibration_by_hour: list[CardCoverage] = Field(default_factory=list)
    serving_range: dict | None = None
    features: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
