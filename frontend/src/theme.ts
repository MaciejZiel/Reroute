/**
 * Colour themes. The page colours live in styles.css as CSS variables (dark by default,
 * `[data-theme="light"]` overrides them); the map cannot read CSS, so its colours are here.
 * Both themes share one identity taken from Warsaw's streets: red for trams, blue for buses and
 * the yellow of the city's trams for whatever you picked. At city scale every route is a quiet
 * grey line; a line gets its colour only when you choose it.
 */

export type Theme = "dark" | "light";

const STORAGE_KEY = "reroute-theme";

export interface BasemapPalette {
  background: string;
  water: string;
  park: string;
  building: string;
  roadMinor: string;
  roadMajor: string;
  roadMotorway: string;
  label: string;
  labelStrong: string;
}

export interface MapPalette {
  basemap: BasemapPalette;
  /** Mode fills: route in focus, vehicles, stop icons. */
  bus: string;
  tram: string;
  /** Context routes at city scale (drawn faint). */
  routeBus: string;
  routeTram: string;
  /** Text and glyphs drawn on a mode fill. */
  onMode: string;
  /** Outline that separates mode fills from the map. */
  outline: string;
  /** Halo behind map text, and the map text itself. */
  halo: string;
  text: string;
  /** The picked station: its icon fill, its outline and its name. */
  picked: string;
  pickedOutline: string;
  pickedText: string;
  /** Ring around the selected vehicle, and the heading wedge fill. */
  selectedRing: string;
  wedge: string;
  delay: { unknown: string; late: string; early: string; onTime: string };
}

export const MAP_PALETTES: Record<Theme, MapPalette> = {
  dark: {
    basemap: {
      background: "#15181b",
      water: "#0e1820",
      park: "#171f1a",
      building: "#1c2024",
      roadMinor: "#22272c",
      roadMajor: "#2c3238",
      roadMotorway: "#343b42",
      label: "#8b939b",
      labelStrong: "#b7bec5",
    },
    bus: "#5b9cf5",
    tram: "#ff6b5e",
    routeBus: "#5d656e",
    routeTram: "#5d656e",
    onMode: "#0d0f12",
    outline: "#0d0f12",
    halo: "#101214",
    text: "#e9ecef",
    picked: "#ffcc33",
    pickedOutline: "#ffffff",
    pickedText: "#ffe39a",
    selectedRing: "#ffffff",
    wedge: "#ffffff",
    delay: { unknown: "#9aa1a9", late: "#ffad42", early: "#a5b4fc", onTime: "#3fcf8e" },
  },
  light: {
    basemap: {
      background: "#eceef0",
      water: "#c3d4e1",
      park: "#dbe7d7",
      building: "#e0e3e6",
      roadMinor: "#ffffff",
      roadMajor: "#ffffff",
      roadMotorway: "#fdfdfd",
      label: "#66707a",
      labelStrong: "#39414a",
    },
    bus: "#1d5bb8",
    tram: "#c62828",
    routeBus: "#9aa2ab",
    routeTram: "#9aa2ab",
    onMode: "#ffffff",
    outline: "#ffffff",
    halo: "#ffffff",
    text: "#1d2329",
    picked: "#f5b800",
    pickedOutline: "#15181c",
    pickedText: "#15181c",
    selectedRing: "#15181c",
    wedge: "#15181c",
    delay: { unknown: "#7c858e", late: "#d97706", early: "#4f5fd0", onTime: "#16a05d" },
  },
};

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** The theme to start with: the visitor's own choice, else the system setting (public/theme-init.js applies the same rule before React loads). */
export function initialTheme(): Theme {
  return stored() ?? (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
}

/** Whether the visitor picked a theme themselves (then system changes no longer apply). */
export function hasStoredTheme() {
  return stored() !== null;
}

export function saveTheme(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private mode or blocked storage: the choice lasts for this visit only.
  }
}

// Keep in step with public/theme-init.js, which sets the same values before the first paint.
const THEME_COLOR: Record<Theme, string> = { dark: "#16191c", light: "#ffffff" };

/** Apply a theme to the page: CSS variables via data-theme, and the browser chrome colour. */
export function applyPageTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
}
