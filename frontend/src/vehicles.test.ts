import { describe, expect, it } from "vitest";
import { currentStep, headingVehicleIds, interpolate, positions, scopeVehicles } from "./vehicles";
import type { Feature, FeatureCollection, Focus, StopDepartures } from "./types";

const NONE: Focus = { stopId: null, stopName: null, stopCoordinate: null, lineId: null, lineLabel: null, vehicleId: null };

const vehicle = (id: string, lng = 21, lat = 52): Feature => ({
  type: "Feature", id, geometry: { type: "Point", coordinates: [lng, lat] }, properties: { line: "4" },
});
const collection = (...features: Feature[]): FeatureCollection => ({
  type: "FeatureCollection", features, data_mode: "live", observed_at: null, notice: "",
});

const departure = (vehicleId: string | null) => ({
  trip_id: `t-${vehicleId}`, stop_id: "s", platform: null, headsign: "X", scheduled_at: "", predicted_at: null,
  delay_seconds: null, minutes: 3, live: vehicleId !== null, vehicle_id: vehicleId, basis: "scheduled" as const,
});
const stop = (lines: StopDepartures["lines"]) => ({ lines }) as StopDepartures;

describe("currentStep", () => {
  it("walks stop, line, vehicle", () => {
    expect(currentStep(NONE)).toBe(1);
    expect(currentStep({ ...NONE, stopId: "s" })).toBe(2);
    expect(currentStep({ ...NONE, stopId: "s", lineId: "4" })).toBe(3);
    expect(currentStep({ ...NONE, stopId: "s", lineId: "4", vehicleId: "V/4/1" })).toBe(3);
  });
});

describe("headingVehicleIds", () => {
  const data = stop([
    { line: "4", route_id: "4", mode: "tram", long_name: "", departures: [departure("V/4/1"), departure(null), departure("V/4/2")] },
    { line: "9", route_id: "9", mode: "tram", long_name: "", departures: [departure("V/9/1")] },
  ]);
  it("collects the live vehicles with a coming departure on that line only", () => {
    expect([...headingVehicleIds(data, "4", "4")].sort()).toEqual(["V/4/1", "V/4/2"]);
  });
  it("is empty without a stop or for an unknown line", () => {
    expect(headingVehicleIds(null, "4", "4").size).toBe(0);
    expect(headingVehicleIds(data, "99", "99").size).toBe(0);
  });
});

describe("scopeVehicles", () => {
  const all = collection(vehicle("a"), vehicle("b"), vehicle("c"));
  it("keeps everything for the whole line", () => {
    expect(scopeVehicles(all, "all", new Set(["a"]), null)).toBe(all);
  });
  it("keeps only the vehicles heading to the stop, plus the picked one", () => {
    expect(scopeVehicles(all, "stop", new Set(["a"]), "c")?.features.map((f) => f.id)).toEqual(["a", "c"]);
  });
  it("passes null through", () => {
    expect(scopeVehicles(null, "stop", new Set(), null)).toBeNull();
  });
});

describe("interpolate", () => {
  const next = collection(vehicle("a", 21.2, 52.2), vehicle("new", 21.5, 52.5));
  const from = positions(collection(vehicle("a", 21, 52)));
  it("moves known vehicles part of the way and snaps new ones", () => {
    const half = interpolate(from, next, 0.5).features;
    const [lng, lat] = (half[0].geometry as GeoJSON.Point).coordinates;
    expect(lng).toBeCloseTo(21.1, 6);
    expect(lat).toBeCloseTo(52.1, 6);
    expect(half[1].geometry).toEqual({ type: "Point", coordinates: [21.5, 52.5] });
  });
  it("clamps the progress", () => {
    expect(interpolate(from, next, 3).features[0].geometry).toEqual({ type: "Point", coordinates: [21.2, 52.2] });
  });
});
