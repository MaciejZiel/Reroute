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
  /** Present only when a trained model answered; the text is English. */
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
