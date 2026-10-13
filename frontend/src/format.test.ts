import { describe, expect, it } from "vitest";
import { firstExplained, preciseDelayText } from "./format";

describe("preciseDelayText", () => {
  it("keeps the seconds so a one-minute range is not shown as +4 … +5 min", () => {
    expect(preciseDelayText(244, "min")).toBe("+4 min 4 s");
    expect(preciseDelayText(305, "min")).toBe("+5 min 5 s");
    expect(preciseDelayText(-45, "min")).toBe("−45 s");
    expect(preciseDelayText(120, "min")).toBe("+2 min");
  });
});

describe("firstExplained", () => {
  it("finds the first stop that has a reason, wherever it is", () => {
    const stops = [{ name: "A", why: null }, { name: "B", why: { summary: "x" } }, { name: "C", why: { summary: "y" } }];
    expect(firstExplained(stops)?.name).toBe("B");
    expect(firstExplained([{ name: "A", why: undefined }])).toBeUndefined();
  });
});
