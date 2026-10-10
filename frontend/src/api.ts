import type { FeatureCollection, PunctualityResult, SimulationResult } from "./types";

const apiBase = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}/api/${path}`);
  if (!response.ok) throw new Error(`API responded with status ${response.status}`);
  return response.json() as Promise<T>;
}

export function fetchNetwork(): Promise<FeatureCollection> {
  return getJson<FeatureCollection>("network");
}

export function fetchVehicles(): Promise<FeatureCollection> {
  return getJson<FeatureCollection>("vehicles");
}

export function fetchPunctuality(minutes: number): Promise<PunctualityResult> {
  return getJson<PunctualityResult>(`punctuality?minutes=${minutes}`);
}

export async function postSimulation(input: {
  target_type: "route" | "stop";
  target_id: string;
  disruption_type: "closure" | "slowdown";
  duration_minutes: number;
  slowdown_factor?: number;
}): Promise<SimulationResult> {
  const response = await fetch(`${apiBase}/api/simulations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API responded with status ${response.status}`);
  }
  return response.json() as Promise<SimulationResult>;
}
