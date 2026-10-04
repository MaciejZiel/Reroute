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
}

export interface SimulationResult {
  id: string;
  target_type: string;
  target_id: string;
  disruption_type: string;
  duration_minutes: number;
  affected_routes: AffectedRoute[];
  affected_stops: string[];
  impact_score: number;
  data_mode: DataMode;
  notice: string;
  created_at: string;
}
