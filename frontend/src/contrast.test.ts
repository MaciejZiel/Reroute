import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MAP_PALETTES, type Theme } from "./theme";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
// The dark tokens are in the first :root block; the light theme overrides them in its own block.
const blocks: Record<Theme, string> = {
  dark: css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {"))),
  light: css.slice(css.indexOf(':root[data-theme="light"] {'), css.indexOf("}", css.indexOf(':root[data-theme="light"] {'))),
};
const token = (theme: Theme, name: string) => {
  const match = blocks[theme].match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`token --${name} not found in the ${theme} theme`);
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

describe.each(["dark", "light"] as Theme[])("%s palette meets WCAG AA", (theme) => {
  const t = (name: string) => token(theme, name);
  const surfaces = ["bg", "surface", "surface-2", "surface-3"];
  it.each(surfaces)("body text tones are at least 4.5:1 on %s", (surface) => {
    for (const text of ["text", "text-2", "muted"]) expect(ratio(t(text), t(surface))).toBeGreaterThanOrEqual(4.5);
  });
  it("mode colours stand out from the map and carry readable text", () => {
    for (const mode of ["bus", "tram"]) {
      expect(ratio(t(mode), MAP_PALETTES[theme].basemap.background)).toBeGreaterThanOrEqual(3); // graphics against the basemap
      expect(ratio(t("on-accent"), t(mode))).toBeGreaterThanOrEqual(4.5); // line number on a chip
    }
    expect(ratio(t("on-gold"), t("gold"))).toBeGreaterThanOrEqual(4.5);
  });
  it("delay and accent text colours are readable on every surface", () => {
    for (const surface of surfaces) {
      for (const colour of ["on-time", "late", "early", "gold-text", "bus"]) expect(ratio(t(colour), t(surface))).toBeGreaterThanOrEqual(4.5);
    }
  });
  it("map vehicles and routes stand out from the basemap", () => {
    const p = MAP_PALETTES[theme];
    for (const colour of [p.bus, p.tram]) expect(ratio(colour, p.basemap.background)).toBeGreaterThanOrEqual(3);
    expect(ratio(p.text, p.halo)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(p.pickedText, p.halo)).toBeGreaterThanOrEqual(4.5);
    for (const colour of [p.bus, p.tram]) expect(ratio(p.onMode, colour)).toBeGreaterThanOrEqual(4.5); // line number on a vehicle
  });
  it("the page and the map use the same mode colours", () => {
    expect(MAP_PALETTES[theme].bus).toBe(t("bus"));
    expect(MAP_PALETTES[theme].tram).toBe(t("tram"));
  });
});
