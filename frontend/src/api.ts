import type {
  FeatureCollection,
  LineDetail,
  PunctualityResult,
  StopDepartures,
  StopSearchResponse,
  VehicleDetail,
} from "./types";

const apiBase = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBase}/api/${path}`, { signal });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(response.status, typeof payload?.detail === "string" ? payload.detail : `API responded with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

const enc = encodeURIComponent;

export const fetchNetwork = (signal?: AbortSignal) => getJson<FeatureCollection>("network", signal);
export const fetchVehicles = (signal?: AbortSignal) => getJson<FeatureCollection>("vehicles", signal);
export const fetchPunctuality = (minutes: number) => getJson<PunctualityResult>(`punctuality?minutes=${minutes}`);
export const searchStops = (query: string, signal?: AbortSignal) => getJson<StopSearchResponse>(`stops?q=${enc(query)}&limit=8`, signal);
export const fetchDepartures = (stopId: string, signal?: AbortSignal) => getJson<StopDepartures>(`stops/${enc(stopId)}/departures?per_line=10`, signal);
export const fetchLine = (line: string, signal?: AbortSignal) => getJson<LineDetail>(`lines/${enc(line)}`, signal);
export const fetchLineVehicles = (line: string, signal?: AbortSignal) => getJson<FeatureCollection>(`lines/${enc(line)}/vehicles`, signal);
// Vehicle ids contain slashes (V/9/032) and the route takes them as a path, so only encode the parts.
export const fetchVehicle = (id: string, language: string, signal?: AbortSignal) => getJson<VehicleDetail>(`vehicles/${id.split("/").map(enc).join("/")}?lang=${language}`, signal);
