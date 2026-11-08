/**
 * The last few stations a visitor opened, kept in this browser only, so going back to "my stop"
 * is one tap from the start panel. Storage can be missing or blocked; then the list is empty.
 */

export interface RecentStop {
  id: string;
  name: string;
  coordinate: [number, number];
}

const STORAGE_KEY = "reroute-recent-stops";
export const MAX_RECENT = 5;

function isRecentStop(value: unknown): value is RecentStop {
  const item = value as RecentStop;
  return typeof item?.id === "string" && typeof item.name === "string" && Array.isArray(item.coordinate)
    && item.coordinate.length === 2 && item.coordinate.every((n) => typeof n === "number" && Number.isFinite(n));
}

export function loadRecentStops(): RecentStop[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isRecentStop).slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

/** The list with this stop first: a station opened again moves up instead of appearing twice. */
export function withRecentStop(list: RecentStop[], stop: RecentStop): RecentStop[] {
  const sameStation = (item: RecentStop) => item.id === stop.id || item.name === stop.name;
  return [stop, ...list.filter((item) => !sameStation(item))].slice(0, MAX_RECENT);
}

export function saveRecentStops(list: RecentStop[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // Private mode or blocked storage: the list lasts for this visit only.
  }
}
