import type { DayType, OddsCell, OddsLine } from "./types";

/** Below this many recorded arrivals an hour says too little to quote a share. */
export const MIN_ARRIVALS = 8;
export const DAY_TYPES: DayType[] = ["weekday", "saturday", "sunday"];

function warsawParts(moment: Date): { weekday: string; hour: number } {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Warsaw", weekday: "short", hour: "2-digit", hourCycle: "h23" }).formatToParts(moment);
  const weekday = parts.find((part) => part.type === "weekday")?.value ?? "Mon";
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  return { weekday, hour };
}

/** The kind of day and the hour of ``moment`` in Warsaw, as the counts are kept. */
export function warsawSlot(moment: Date): { day: DayType; hour: number } {
  const { weekday, hour } = warsawParts(moment);
  const day: DayType = weekday === "Sat" ? "saturday" : weekday === "Sun" ? "sunday" : "weekday";
  return { day, hour };
}

export function cellAt(line: OddsLine, day: DayType, hour: number): OddsCell | undefined {
  return line.cells.find((cell) => cell.day_type === day && cell.hour === hour);
}

/** Colour step of an hour on the heat map: null without enough arrivals, then 0 (rarely late)
 * to 3 (late more than 3 minutes in at least 3 trips out of 10). */
export function heatLevel(cell: OddsCell | undefined): 0 | 1 | 2 | 3 | null {
  if (!cell || cell.n < MIN_ARRIVALS) return null;
  const share = cell.late3_share;
  if (share < 0.05) return 0;
  if (share < 0.15) return 1;
  if (share < 0.3) return 2;
  return 3;
}

/** The columns of the heat map: always 5:00 to 23:00, so lines compare at a glance, plus any
 * night hour the line runs, laid out as a service day that runs past midnight (4:00 to 3:00). */
export function serviceHours(line: OddsLine): number[] {
  const order = (hour: number) => (hour + 24 - 4) % 24;
  const present = line.cells.filter((cell) => cell.n > 0).map((cell) => cell.hour);
  const all = [...new Set([...Array.from({ length: 19 }, (_, i) => i + 5), ...present])];
  const ordered = all.map(order);
  const first = Math.min(...ordered), last = Math.max(...ordered);
  return Array.from({ length: last - first + 1 }, (_, i) => (first + i + 4) % 24);
}

/** "1 in 6" style odds from a share, rounded so it reads naturally; null when below 1 in 50. */
export function oneIn(share: number): number | null {
  if (share <= 0.02) return null;
  return Math.max(1, Math.round(1 / share));
}
