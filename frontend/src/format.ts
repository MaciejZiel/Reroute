import type { Language } from "./types";

export type DelayClass = "early" | "on-time" | "late" | "unknown";

// Same limits as the backend punctuality view: early below 1 min ahead, late above 3 min behind.
export function delayClass(seconds: number | null | undefined): DelayClass {
  if (seconds === null || seconds === undefined) return "unknown";
  if (seconds < -60) return "early";
  if (seconds > 180) return "late";
  return "on-time";
}

/** "+3 min", "−1 min" or "0 min" for a delay in seconds. */
export function delayText(seconds: number, minuteLabel: string): string {
  const minutes = Math.round(seconds / 60);
  if (minutes === 0) return `0 ${minuteLabel}`;
  return `${minutes > 0 ? "+" : "−"}${Math.abs(minutes)} ${minuteLabel}`;
}

export function clock(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", {
    hour: "2-digit", minute: "2-digit", timeZone: "Europe/Warsaw",
  }).format(new Date(iso));
}

/** Natural line order: 2, 9, 10, 175, then letters first (L1, N63). */
export function lineOrder(a: string, b: string): number {
  return a.localeCompare(b, "en", { numeric: true });
}

export function geometryBounds(geometry: GeoJSON.Geometry | null): [[number, number], [number, number]] | null {
  if (!geometry) return null;
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
  const visit = (value: unknown) => {
    if (Array.isArray(value) && typeof value[0] === "number") {
      const [lng, lat] = value as number[];
      west = Math.min(west, lng); east = Math.max(east, lng);
      south = Math.min(south, lat); north = Math.max(north, lat);
    } else if (Array.isArray(value)) value.forEach(visit);
  };
  if ("coordinates" in geometry) visit(geometry.coordinates);
  return Number.isFinite(west) ? [[west, south], [east, north]] : null;
}
