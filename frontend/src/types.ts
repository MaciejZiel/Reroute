export type Language = "pl" | "en";
export type DataMode = "live" | "demo";
export type EntityType = "route" | "stop";

export interface Feature {
  type: "Feature";
  id: string;
  geometry: GeoJSON.Geometry;
  properties: Record<string, string | number | boolean | null>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: Feature[];
  data_mode: DataMode;
  observed_at: string | null;
  notice: string;
}

export interface StopSummary {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  platform: string | null;
}

export interface StopMatch {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  stop_ids: string[];
  lines: string[];
}

export interface StopSearchResponse {
  query: string;
  results: StopMatch[];
}

export interface Departure {
  trip_id: string;
  stop_id: string;
  platform: string | null;
  headsign: string;
  scheduled_at: string;
  predicted_at: string | null;
  delay_seconds: number | null;
  minutes: number;
  live: boolean;
  vehicle_id: string | null;
  basis: "live_delay" | "scheduled";
  /** Only with a trained model: "model" or "held" when the model estimated predicted_at, else the baseline did. */
  model_status?: "model" | "held" | "paused" | "out_of_range" | "passed" | null;
}

export interface LineDepartures {
  line: string;
  route_id: string;
  mode: string;
  long_name: string;
  departures: Departure[];
}

export interface StopDepartures {
  stop: StopSummary;
  platforms: StopSummary[];
  generated_at: string;
  horizon_minutes: number;
  data_mode: DataMode;
  live_vehicles: number;
  lines: LineDepartures[];
  notice: string;
}

export interface LineStop {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  sequence: number;
}

export interface AlertInfo {
  id: string;
  effect: string;
  header_pl: string | null;
  header_en: string | null;
  url: string | null;
}

export interface LineDetail {
  line: string;
  route_id: string;
  long_name: string;
  mode: string;
  shape: GeoJSON.Geometry | null;
  stops: LineStop[];
  alerts: AlertInfo[];
  data_mode: DataMode;
}

export interface NextStop {
  stop_id: string;
  name: string;
  sequence: number;
  scheduled_at: string;
  predicted_at: string | null;
  delay_seconds: number | null;
  lower_delay_seconds?: number | null;
  upper_delay_seconds?: number | null;
  /** Only with a trained model: it answered, kept its last answer ("held"), the baseline answered ("paused"), or the vehicle is already at or past the stop ("passed"). */
  model_status?: "model" | "held" | "paused" | "out_of_range" | "passed" | null;
  pause_reason?: "no_vehicle" | "vehicle_report" | "last_stop" | "no_stop" | null;
  /** Present only when a trained model answered; the text is in the requested language. */
  why?: { summary: string; factors: { feature: string; effect_seconds: number; text: string }[] } | null;
}

export interface VehicleDetail {
  vehicle_id: string;
  label: string | null;
  line: string;
  mode: string;
  trip_id: string | null;
  headsign: string | null;
  observed_at: string;
  age_seconds: number;
  latitude: number;
  longitude: number;
  delay_seconds: number | null;
  status: "ok" | "layover" | "off_route" | "no_schedule" | "stale";
  next_stops: NextStop[];
  model: { name: string; version: string; kind: "baseline" | "model" };
}

/** What the user has picked: a stop, then optionally one of its lines, then optionally a vehicle. */
export interface Focus {
  stopId: string | null;
  stopName: string | null;
  stopCoordinate: [number, number] | null;
  lineId: string | null;
  lineLabel: string | null;
  vehicleId: string | null;
}

export interface PunctualityPoint {
  observed_at: string;
  vehicles: number;
  mean_delay_minutes: number;
  on_time_share: number;
}

export interface LinePunctuality {
  line: string;
  mode: string;
  observations: number;
  mean_delay_minutes: number;
  median_delay_minutes: number;
  early_share: number;
  on_time_share: number;
  late_share: number;
  series: PunctualityPoint[];
}

export interface PunctualityResult {
  data_mode: DataMode;
  window_minutes: number;
  snapshots: number;
  observed_from: string | null;
  observed_to: string | null;
  on_time_definition: string;
  lines: LinePunctuality[];
  notice: string;
}

export interface CardInterval { value: number; low: number; high: number }

export interface CardScore {
  name: string;
  mae_s: CardInterval;
  rmse_s: CardInterval;
  bias_s: number;
  mae_by_horizon_s: Record<string, number>;
}

export interface CardCoverage {
  label: string;
  predictions: number;
  coverage: CardInterval;
  held_out: boolean;
  window: string;
}

/** GET /api/model: the record of the one training run, static. */
export interface ModelCard {
  status: "model" | "baseline";
  version: string | null;
  trained_at: string | null;
  promoted: boolean | null;
  training_window: { from: string; to: string } | null;
  test_window: { from: string; to: string } | null;
  data: { arrivals?: number; trips?: number; lines?: number; stops?: number; first_arrival?: string; last_arrival?: string; span_hours?: number; service_dates?: string[] } | null;
  best_baseline: string | null;
  gain_vs_best_baseline: CardInterval | null;
  gain_vs_ridge: CardInterval | null;
  scores: CardScore[];
  interval_nominal: number | null;
  calibration_by_horizon: CardCoverage[];
  calibration_by_hour: CardCoverage[];
  serving_range: { max_stops_ahead?: number } | null;
}
