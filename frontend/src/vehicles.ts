import type { Feature, FeatureCollection, Focus, StopDepartures } from "./types";

/** Which vehicles of the picked line to show: all of them, or only those still heading to the picked stop. */
export type LineScope = "all" | "stop";

/** The three steps of the flow: pick a stop, pick a line, pick a vehicle. */
export type Step = 1 | 2 | 3;

/** Step the user is on now: the first one that is still to do (3 once a vehicle is picked). */
export function currentStep(focus: Focus): Step {
  if (focus.vehicleId) return 3;
  if (focus.lineId) return 3;
  if (focus.stopId) return 2;
  return 1;
}

/**
 * Ids of the live vehicles that have a coming departure at the picked stop on the picked line.
 * The departures list only contains vehicles that have not passed the stop yet, so this is
 * "heading to my stop" without having to compare directions.
 */
export function headingVehicleIds(stop: StopDepartures | null, lineId: string | null, lineLabel: string | null): Set<string> {
  const line = stop?.lines.find((item) => item.route_id === lineId || item.line === lineLabel);
  const ids = new Set<string>();
  for (const departure of line?.departures ?? []) if (departure.vehicle_id) ids.add(departure.vehicle_id);
  return ids;
}

/** Apply the line scope. A vehicle that is picked always stays, so the map never drops what the panel shows. */
export function scopeVehicles(
  vehicles: FeatureCollection | null,
  scope: LineScope,
  heading: ReadonlySet<string>,
  selectedId: string | null,
): FeatureCollection | null {
  if (!vehicles || scope === "all") return vehicles;
  const features: Feature[] = vehicles.features.filter((feature) => heading.has(feature.id) || feature.id === selectedId);
  return { ...vehicles, features };
}

/** Positions between two snapshots; vehicles that were not in the previous one jump straight to the new position. */
export function interpolate(from: Map<string, [number, number]>, to: FeatureCollection, progress: number): FeatureCollection {
  const t = Math.min(1, Math.max(0, progress));
  return {
    ...to,
    features: to.features.map((feature) => {
      const before = from.get(feature.id);
      if (!before || feature.geometry.type !== "Point") return feature;
      const [lng, lat] = feature.geometry.coordinates;
      return { ...feature, geometry: { type: "Point", coordinates: [before[0] + (lng - before[0]) * t, before[1] + (lat - before[1]) * t] } };
    }),
  };
}

export function positions(collection: FeatureCollection | null): Map<string, [number, number]> {
  const result = new Map<string, [number, number]>();
  for (const feature of collection?.features ?? []) {
    if (feature.geometry.type === "Point") result.set(feature.id, [feature.geometry.coordinates[0], feature.geometry.coordinates[1]]);
  }
  return result;
}
