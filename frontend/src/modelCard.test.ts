import { describe, expect, it } from "vitest";
import { comparedScores, coverageVerdict, hoursByWindow, horizonLabel, percentText, warsawClock } from "./modelCard";
import type { CardCoverage, CardScore } from "./types";

const score = (name: string, mae: number): CardScore => ({
  name, mae_s: { value: mae, low: mae - 1, high: mae + 1 }, rmse_s: { value: mae, low: mae, high: mae }, bias_s: 0, mae_by_horizon_s: {},
});

describe("comparedScores", () => {
  it("picks persistence, ridge and trees in a fixed order and skips the rest", () => {
    const picked = comparedScores([
      score("scheduled (zero delay)", 104),
      score("gradient boosted trees (median)", 40.6),
      score("ridge regression (same features)", 46),
      score("persistence (BaselinePredictor)", 44.5),
    ]);
    expect(picked.map((item) => item.key)).toEqual(["predPersistence", "predLinear", "predTrees"]);
    expect(picked[2].score.mae_s.value).toBe(40.6);
  });

  it("adds the best baseline after persistence when it is another predictor", () => {
    const scores = [
      score("persistence (BaselinePredictor)", 93.6),
      score("calibrated persistence", 83.8),
      score("ridge regression (same features)", 79.7),
      score("gradient boosted trees (median)", 74.4),
    ];
    const picked = comparedScores(scores, "calibrated persistence");
    expect(picked.map((item) => item.key)).toEqual(["predPersistence", "predBestBaseline", "predLinear", "predTrees"]);
    expect(picked[1].score.mae_s.value).toBe(83.8);
    expect(comparedScores(scores, "persistence (BaselinePredictor)").map((item) => item.key)).toEqual(["predPersistence", "predLinear", "predTrees"]);
  });

  it("copes with a card that has no ridge row (an older model file)", () => {
    const picked = comparedScores([score("persistence (BaselinePredictor)", 44.5), score("gradient boosted trees (median)", 40.6)]);
    expect(picked.map((item) => item.key)).toEqual(["predPersistence", "predTrees"]);
    expect(comparedScores([])).toEqual([]);
  });
});

describe("number and label formatting", () => {
  it("uses the decimal comma in Polish and the point in English", () => {
    expect(percentText("en", 0.799)).toBe("79.9%");
    expect(percentText("pl", 0.799)).toBe("79,9%");
  });
  it("translates horizon labels and leaves unknown ones as they are", () => {
    expect(horizonLabel("pl", "under 2 min")).toBe("poniżej 2 min");
    expect(horizonLabel("pl", "something new")).toBe("something new");
    expect(horizonLabel("en", "under 2 min")).toBe("under 2 min");
  });
  it("tells the evening in Warsaw time", () => {
    expect(warsawClock("2026-10-13T19:14:11Z", "en")).toBe("21:14");
    expect(warsawClock("2026-10-29T10:59:47Z", "pl")).toBe("12:00");
  });
});

describe("coverage", () => {
  it("is close to the promise within three points and off beyond", () => {
    expect(coverageVerdict(0.799, 0.8)).toBe("close");
    expect(coverageVerdict(0.77, 0.8)).toBe("close");
    expect(coverageVerdict(0.7, 0.8)).toBe("off");
  });
  it("separates held-out hours from the windows the model was tuned on", () => {
    const row = (label: string, held_out: boolean): CardCoverage => ({ label, predictions: 1, coverage: { value: 0.8, low: 0.7, high: 0.9 }, held_out, window: held_out ? "held out" : "calibration window" });
    const split = hoursByWindow([row("20:00", false), row("21:00", true), row("22:00", true)]);
    expect(split.heldOut.map((r) => r.label)).toEqual(["21:00", "22:00"]);
    expect(split.tuned.map((r) => r.label)).toEqual(["20:00"]);
  });
});
