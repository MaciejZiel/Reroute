import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const token = (name: string) => {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`token --${name} not found`);
  return match[1];
};

function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function ratio(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("palette meets WCAG AA", () => {
  const surfaces = ["bg", "surface", "surface-2", "surface-3"];
  it.each(surfaces)("body text tones are at least 4.5:1 on %s", (surface) => {
    for (const text of ["text", "text-2", "muted"]) expect(ratio(token(text), token(surface))).toBeGreaterThanOrEqual(4.5);
  });
  it("mode colours stand out from the map and carry dark text", () => {
    for (const mode of ["bus", "tram"]) {
      expect(ratio(token(mode), "#111918")).toBeGreaterThanOrEqual(3); // graphics against the basemap
      expect(ratio("#0e1413", token(mode))).toBeGreaterThanOrEqual(4.5); // line number on a chip
    }
  });
  it("delay colours are readable on the dark surfaces", () => {
    for (const delay of ["on-time", "late", "early"]) expect(ratio(token(delay), token("bg"))).toBeGreaterThanOrEqual(4.5);
  });
});
