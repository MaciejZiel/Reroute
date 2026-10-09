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

export interface Selection {
  type: EntityType;
  id: string;
  label: string;
  coordinate: [number, number];
}

export interface AffectedRoute {
  id: string;
  label: string;
  mode: string;
  affected_stops: number;
  affected_trips: number;
}

export interface JourneyLeg {
  kind: "ride" | "walk";
  line: string | null;
  mode: string | null;
  from_stop: string;
  to_stop: string;
  minutes: number;
  wait_minutes: number;
  stops: number;
}

export interface RouteLabel {
  id: string;
  label: string;
  mode: string;
}

export interface Detour {
  lines: RouteLabel[];
  origin: string;
  destination: string;
  baseline_minutes: number;
  disrupted_minutes: number | null;
  added_minutes: number | null;
  legs: JourneyLeg[];
}

export interface RoutingSummary {
  affected_trips: number;
  affected_segments: number;
  sampled_journeys: number;
  average_added_minutes: number | null;
  max_added_minutes: number | null;
  unreachable_journeys: number;
  slowdown_factor: number | null;
  closed_stop_ids: string[];
}

export interface AlternativeRoute {
  id: string;
  label: string;
  mode: string;
  shared_stops: number;
}

export interface AlternativeStop {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  distance_m: number;
  routes: string[];
}

export interface SimulationResult {
  id: string;
  target_type: string;
  target_id: string;
  disruption_type: string;
  duration_minutes: number;
  affected_routes: AffectedRoute[];
  affected_stops: string[];
  alternative_routes: AlternativeRoute[];
  alternative_stops: AlternativeStop[];
  routing: RoutingSummary;
  detours: Detour[];
  affected_stop_count: number;
  impact_score: number;
  data_mode: DataMode;
  notice: string;
  created_at: string;
}
