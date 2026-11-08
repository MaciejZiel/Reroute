import { describe, expect, it } from "vitest";
import { distanceM, distanceText, nearestStations } from "./nearby";
import type { FeatureCollection } from "./types";

const stop = (id: string, name: string, lng: number, lat: number) => ({
  type: "Feature" as const, id, geometry: { type: "Point" as const, coordinates: [lng, lat] }, properties: { entity_type: "stop", name },
});
const network = {
  type: "FeatureCollection", data_mode: "live", observed_at: null, notice: "",
  features: [
    stop("1", "Centrum", 21.0122, 52.2297),
    stop("2", "Centrum", 21.0105, 52.2302), // another platform of the same station, slightly further
    stop("3", "Świętokrzyska", 21.0080, 52.2350),
    stop("4", "Wilanów", 21.0900, 52.1650), // far away
  ],
} as unknown as FeatureCollection;

describe("nearby stations", () => {
  it("measures distance on the globe", () => {
    expect(Math.round(distanceM([21, 52], [21, 52.01]))).toBe(1112);
  });
  it("lists each station once, by its nearest platform, nearest first, within reach", () => {
    const list = nearestStations(network, [21.0120, 52.2296]);
    expect(list.map((s) => s.name)).toEqual(["Centrum", "Świętokrzyska"]);
    expect(list[0].id).toBe("1");
  });
  it("writes distances the way people say them", () => {
    expect(distanceText(347, "pl")).toBe("350 m");
    expect(distanceText(1234, "pl")).toBe("1,2 km");
    expect(distanceText(1234, "en")).toBe("1.2 km");
  });
});
