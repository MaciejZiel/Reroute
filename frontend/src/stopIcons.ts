import type { Map as MapLibreMap } from "maplibre-gl";
import type { MapPalette } from "./theme";

export type StopKind = "bus" | "tram" | "both";
export const STOP_KINDS: StopKind[] = ["bus", "tram", "both"];

const SIZE = 28; // logical pixels; drawn at 2x so it stays sharp on high-density screens
const RATIO = 2;

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// On gold (a picked stop) the glyph is always dark ink; on a mode colour it is the theme's ink for that colour.
const INK = "#0e1413";

function glyph(ctx: CanvasRenderingContext2D, kind: StopKind, cx: number, cy: number, ink: string, window: string) {
  ctx.fillStyle = ink;
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1.6;
  ctx.lineCap = "round";
  if (kind === "tram") {
    roundedRect(ctx, cx - 5, cy - 3.5, 10, 9, 2);
    ctx.fill();
    ctx.beginPath(); ctx.moveTo(cx - 3, cy - 3.5); ctx.lineTo(cx, cy - 7); ctx.lineTo(cx + 3, cy - 3.5); ctx.stroke(); // pantograph
    ctx.fillStyle = window; ctx.fillRect(cx - 3.5, cy - 1.5, 7, 2.6); // window band
  } else if (kind === "bus") {
    roundedRect(ctx, cx - 5.5, cy - 5.5, 11, 10.5, 2.4);
    ctx.fill();
    ctx.fillStyle = window; ctx.fillRect(cx - 4, cy - 3.8, 8, 3.2); // windscreen
    ctx.fillStyle = ink; ctx.fillRect(cx - 4.2, cy + 4, 2.4, 2.2); ctx.fillRect(cx + 1.8, cy + 4, 2.4, 2.2); // wheels
  } else {
    // Both modes: two stacked bars, the shorthand for "several services".
    roundedRect(ctx, cx - 5.5, cy - 5.5, 11, 4.6, 1.6); ctx.fill();
    roundedRect(ctx, cx - 5.5, cy + 0.9, 11, 4.6, 1.6); ctx.fill();
  }
}

/** Draw one stop icon: a rounded tile in the mode colour(s) with an outline and a small vehicle glyph. */
export function drawStopIcon(kind: StopKind, selected: boolean, palette: MapPalette): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = SIZE * RATIO;
  canvas.height = SIZE * RATIO;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas is not available");
  ctx.scale(RATIO, RATIO);
  const pad = selected ? 1.5 : 3;
  const size = SIZE - pad * 2;
  ctx.save();
  roundedRect(ctx, pad, pad, size, size, 7);
  ctx.fillStyle = selected ? palette.picked : kind === "tram" ? palette.tram : palette.bus;
  ctx.fill();
  if (kind === "both" && !selected) {
    ctx.clip();
    ctx.fillStyle = palette.tram;
    ctx.beginPath(); ctx.moveTo(SIZE, 0); ctx.lineTo(SIZE, SIZE); ctx.lineTo(0, SIZE); ctx.closePath(); ctx.fill();
  }
  ctx.restore();
  roundedRect(ctx, pad, pad, size, size, 7);
  ctx.lineWidth = selected ? 3 : 2;
  ctx.strokeStyle = selected ? palette.pickedOutline : palette.outline;
  ctx.stroke();
  const fill = selected ? palette.picked : kind === "tram" ? palette.tram : palette.bus;
  if (selected) glyph(ctx, kind, SIZE / 2, SIZE / 2 + 0.5, INK, "#ffffff");
  else glyph(ctx, kind, SIZE / 2, SIZE / 2 + 0.5, palette.onMode, fill);
  return ctx.getImageData(0, 0, SIZE * RATIO, SIZE * RATIO);
}

/** A wedge pointing up and sitting outside the vehicle dot; rotated by the bearing it shows the heading. */
export function drawHeadingWedge(palette: MapPalette): ImageData {
  const size = 96;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas is not available");
  ctx.fillStyle = palette.wedge;
  ctx.strokeStyle = palette.halo;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.beginPath(); ctx.moveTo(48, 5); ctx.lineTo(58, 21); ctx.lineTo(38, 21); ctx.closePath();
  ctx.stroke(); ctx.fill();
  return ctx.getImageData(0, 0, size, size);
}

export function stopIconId(kind: StopKind, selected = false) {
  return `stop-${kind}${selected ? "-selected" : ""}`;
}

/** Add the icons, or redraw them in place for a new palette (same size, so updateImage applies). */
export function registerIcons(map: MapLibreMap, palette: MapPalette) {
  const put = (id: string, image: ImageData, pixelRatio: number) => {
    if (map.hasImage(id)) map.updateImage(id, image);
    else map.addImage(id, image, { pixelRatio });
  };
  for (const kind of STOP_KINDS) {
    for (const selected of [false, true]) put(stopIconId(kind, selected), drawStopIcon(kind, selected, palette), RATIO);
  }
  put("heading", drawHeadingWedge(palette), 2);
}
