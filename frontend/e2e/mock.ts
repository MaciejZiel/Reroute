import type { Page } from "@playwright/test";

const STOP = { id: "100101", name: "Rondo Daszyńskiego", latitude: 52.2, longitude: 21.0, stop_ids: ["100101"], lines: ["9", "10"] };
const empty = { type: "FeatureCollection", features: [], data_mode: "live", observed_at: null, notice: "" };

const departures = {
  stop: { id: "100101", name: "Rondo Daszyńskiego", latitude: 52.2, longitude: 21.0 }, platforms: [],
  generated_at: "2026-10-13T12:00:00Z", horizon_minutes: 120, data_mode: "live", live_vehicles: 1, notice: "",
  lines: [{ line: "9", route_id: "9", mode: "tram", long_name: "Łazienki - Koniec", departures: [
    // the model's forecast, the baseline's estimate and a trip with no vehicle yet
    { trip_id: "d1", stop_id: "100101", platform: "01", headsign: "Koniec", scheduled_at: "2026-10-13T12:02:00Z", predicted_at: "2026-10-13T12:04:00Z", delay_seconds: 120, minutes: 4, live: true, vehicle_id: null, basis: "live_delay", model_status: "model" },
    { trip_id: "d2", stop_id: "100101", platform: "01", headsign: "Koniec", scheduled_at: "2026-10-13T12:12:00Z", predicted_at: "2026-10-13T12:13:00Z", delay_seconds: 60, minutes: 13, live: true, vehicle_id: null, basis: "live_delay", model_status: "paused" },
    { trip_id: "d3", stop_id: "100101", platform: "01", headsign: "Koniec", scheduled_at: "2026-10-13T12:22:00Z", predicted_at: null, delay_seconds: null, minutes: 22, live: false, vehicle_id: null, basis: "scheduled" },
  ] }],
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

const iv = (value: number, low: number, high: number) => ({ value, low, high });
const score = (name: string, mae: number) => ({ name, mae_s: iv(mae, mae - 1, mae + 1), rmse_s: iv(mae * 1.4, mae * 1.3, mae * 1.5), bias_s: 0, mae_by_horizon_s: {} });
const coverage = (label: string, predictions: number, value: number, held_out = true) => ({ label, predictions, coverage: iv(value, value - 0.01, value + 0.01), held_out, window: held_out ? "held out" : "calibration window" });

export const MODEL_CARD = {
  status: "model", version: "20261013-f2a7a97f", trained_at: "2026-10-13T20:11:25Z", promoted: true,
  training_window: { from: "2026-10-13T14:26:46Z", to: "2026-10-13T19:14:11Z" },
  test_window: { from: "2026-10-13T19:14:11Z", to: "2026-10-13T20:08:56Z" },
  data: { arrivals: 240998, trips: 10348, lines: 265, stops: 6422, first_arrival: "2026-10-13T14:25:09Z", last_arrival: "2026-10-13T20:08:56Z", span_hours: 5.73, service_dates: ["2026-10-13"] },
  best_baseline: "persistence (BaselinePredictor)", gain_vs_best_baseline: iv(0.087, 0.073, 0.101), gain_vs_ridge: iv(0.117, 0.106, 0.129),
  scores: [score("persistence (BaselinePredictor)", 44.5), score("ridge regression (same features)", 46), score("gradient boosted trees (median)", 40.6)],
  interval_nominal: 0.8,
  calibration_by_horizon: [coverage("under 2 min", 11859, 0.788), coverage("2 to 5 min", 62156, 0.792)],
  calibration_by_hour: [coverage("21:00", 151443, 0.799), coverage("22:00", 13282, 0.798), coverage("20:00", 3000, 0.81, false)],
  serving_range: { max_stops_ahead: 12 },
};

/** Mock the whole API: a stop search with several hits, one line with one vehicle. */
export async function mockApi(page: Page, vehicle: (url: URL) => unknown) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api\//, "");
    let body: unknown = {};
    if (path === "network" || path === "vehicles") body = empty;
    else if (path === "model") body = MODEL_CARD;
    else if (path === "stops") {
      body = { query: url.searchParams.get("q"), results: [1, 2, 3, 4].map((n) => ({ ...STOP, id: `10010${n}`, name: `Rondo ${n}` })) };
    } else if (path.startsWith("stops/")) body = departures;
    else if (path === "lines/9") body = line;
    else if (path === "lines/9/vehicles") body = lineVehicles;
    else if (path.startsWith("vehicles/")) body = vehicle(url);
    await route.fulfill({ json: body });
  });
}
