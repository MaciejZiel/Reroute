import type { Page } from "@playwright/test";

const STOP = { id: "100101", name: "Rondo Daszyńskiego", latitude: 52.2, longitude: 21.0, stop_ids: ["100101"], lines: ["9", "10"] };
const empty = { type: "FeatureCollection", features: [], data_mode: "live", observed_at: null, notice: "" };

const departures = {
  stop: { id: "100101", name: "Rondo Daszyńskiego", latitude: 52.2, longitude: 21.0 }, platforms: [],
  generated_at: "2026-10-13T12:00:00Z", horizon_minutes: 120, data_mode: "live", live_vehicles: 1, notice: "",
  lines: [{ line: "9", route_id: "9", mode: "tram", long_name: "Łazienki - Koniec", departures: [] }],
};
const line = { line: "9", route_id: "9", long_name: "Łazienki - Koniec", mode: "tram", shape: null, stops: [], alerts: [], data_mode: "live" };
const lineVehicles = {
  ...empty,
  features: [{ type: "Feature", id: "V/9/23", geometry: { type: "Point", coordinates: [21.0, 52.2] }, properties: { label: "3023", line: "9", delay_seconds: 89, entity_type: "vehicle" } }],
};

export function vehicleDetail(nextStop: Record<string, unknown>) {
  return {
    vehicle_id: "V/9/23", label: "3023", line: "9", mode: "tram", trip_id: "t", headsign: "Koniec",
    observed_at: "2026-10-13T12:00:00Z", age_seconds: 5, latitude: 52.2, longitude: 21.0, delay_seconds: 89, status: "ok",
    next_stops: [{
      stop_id: "200101", name: "Rondo ONZ", sequence: 3, scheduled_at: "2026-10-13T12:05:00Z",
      predicted_at: "2026-10-13T12:06:30Z", delay_seconds: 89, ...nextStop,
    }],
    model: { name: "gbt-delay", version: "test", kind: "model" },
  };
}

/** Mock the whole API: a stop search with several hits, one line with one vehicle. */
export async function mockApi(page: Page, vehicle: (url: URL) => unknown) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\//, "");
    let body: unknown = {};
    if (path === "network" || path === "vehicles") body = empty;
    else if (path === "stops") {
      body = { query: url.searchParams.get("q"), results: [1, 2, 3, 4].map((n) => ({ ...STOP, id: `10010${n}`, name: `Rondo ${n}` })) };
    } else if (path.startsWith("stops/")) body = departures;
    else if (path === "lines/9") body = line;
    else if (path === "lines/9/vehicles") body = lineVehicles;
    else if (path.startsWith("vehicles/")) body = vehicle(url);
    await route.fulfill({ json: body });
  });
}
