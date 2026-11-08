import type { FeatureCollection } from "./types";

export interface NearbyStop {
  id: string;
  name: string;
  coordinate: [number, number];
  distanceM: number;
}

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance in metres between two [longitude, latitude] points. */
export function distanceM(a: [number, number], b: [number, number]): number {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad;
  const dLng = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
}

/**
 * The closest stations to a point, one entry per station name (its nearest platform), from the
 * network the map already holds, so no request is needed. Stations further than maxM are left out.
 */
export function nearestStations(network: FeatureCollection | null, here: [number, number], count = 5, maxM = 2_000): NearbyStop[] {
  if (!network) return [];
  const best = new Map<string, NearbyStop>();
  for (const feature of network.features) {
    if (feature.properties.entity_type !== "stop" || feature.geometry.type !== "Point") continue;
    const coordinate = feature.geometry.coordinates as [number, number];
    const d = distanceM(here, coordinate);
    if (d > maxM) continue;
    const name = String(feature.properties.name ?? "");
    const current = best.get(name);
    if (!current || d < current.distanceM) best.set(name, { id: String(feature.id), name, coordinate, distanceM: d });
  }
  return [...best.values()].sort((a, b) => a.distanceM - b.distanceM).slice(0, count);
}

/** "350 m" or "1.2 km" (decimal comma in Polish). */
export function distanceText(metres: number, language: "pl" | "en"): string {
  if (metres < 1_000) return `${Math.max(10, Math.round(metres / 10) * 10)} m`;
  const km = (metres / 1_000).toFixed(1);
  return `${language === "pl" ? km.replace(".", ",") : km} km`;
}
