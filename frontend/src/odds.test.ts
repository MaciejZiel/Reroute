import { describe, expect, it } from "vitest";
import { cellAt, heatLevel, oneIn, serviceHours, warsawSlot } from "./odds";
import type { OddsCell, OddsLine } from "./types";

const cell = (day: OddsCell["day_type"], hour: number, n: number, late3: number): OddsCell => ({
  day_type: day, hour, n, days: 3, late3_share: late3, late5_share: late3 / 2, early_share: 0, median_s: 30, p90_s: 200,
});
const line: OddsLine = { line: "4", route_id: "4", mode: "tram", stop_id: "700107", platform: "07", headsign: "Al. Lotników",
  cells: [cell("weekday", 5, 20, 0.01), cell("weekday", 8, 40, 0.2), cell("saturday", 0, 9, 0.5), cell("sunday", 23, 3, 0.9)] };

describe("odds", () => {
  it("names the Warsaw kind of day and hour, across the time change", () => {
    expect(warsawSlot(new Date("2026-11-07T20:30:00Z"))).toEqual({ day: "saturday", hour: 21 });
    expect(warsawSlot(new Date("2026-07-06T06:00:00Z"))).toEqual({ day: "weekday", hour: 8 });
    expect(warsawSlot(new Date("2026-11-08T23:30:00Z"))).toEqual({ day: "weekday", hour: 0 }); // Monday 00:30
  });
  it("colours an hour only with enough arrivals", () => {
    expect(heatLevel(cellAt(line, "weekday", 5))).toBe(0);
    expect(heatLevel(cellAt(line, "weekday", 8))).toBe(2);
    expect(heatLevel(cellAt(line, "saturday", 0))).toBe(3);
    expect(heatLevel(cellAt(line, "sunday", 23))).toBeNull(); // 3 arrivals
    expect(heatLevel(undefined)).toBeNull();
  });
  it("lays the hours out as a service day that runs past midnight", () => {
    expect(serviceHours(line)).toEqual([5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0]);
    expect(serviceHours({ ...line, cells: [cell("weekday", 8, 40, 0.5)] })).toHaveLength(19); // 5:00 to 23:00
  });
  it("turns a share into plain odds", () => {
    expect(oneIn(0.18)).toBe(6);
    expect(oneIn(0.5)).toBe(2);
    expect(oneIn(0.01)).toBeNull();
  });
});
