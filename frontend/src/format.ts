import type { Language } from "./types";

export type DelayClass = "early" | "on-time" | "late" | "unknown";

// Same limits as the backend punctuality view: early below 1 min ahead, late above 3 min behind.
export function delayClass(seconds: number | null | undefined): DelayClass {
  if (seconds === null || seconds === undefined) return "unknown";
  if (seconds < -60) return "early";
  if (seconds > 180) return "late";
  return "on-time";
}

/** "+3 min 20 s", "−45 s": a delay to the second, for ranges that are only a minute wide. */
export function preciseDelayText(seconds: number, minuteLabel: string): string {
  const total = Math.round(Math.abs(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  const body = minutes === 0 ? `${rest} s` : rest === 0 ? `${minutes} ${minuteLabel}` : `${minutes} ${minuteLabel} ${rest} s`;
  return `${seconds < 0 ? "−" : "+"}${body}`;
}

/** The first stop of a list that carries a model explanation: not necessarily the first stop. */
export function firstExplained<T extends { why?: unknown }>(stops: T[]): T | undefined {
  return stops.find((stop) => stop.why);
}

/** The key of the calm note shown when the baseline answers the first stop instead of the model, if any. */
export function pausedNoteKey(stops: { model_status?: string | null; pause_reason?: string | null; why?: unknown }[]):
  "pausedVehicleReport" | "pausedLastStop" | "passedNote" | null {
  const first = stops[0];
  if (!first || first.why) return null;
  if (first.model_status === "passed") return "passedNote";
  if (first.model_status !== "paused") return null;
  if (first.pause_reason === "vehicle_report") return "pausedVehicleReport";
  if (first.pause_reason === "last_stop") return "pausedLastStop";
  return null;
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
