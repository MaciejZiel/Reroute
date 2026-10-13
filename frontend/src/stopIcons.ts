import type { Map as MapLibreMap } from "maplibre-gl";

export const BUS_COLOR = "#6fd9b6";
export const TRAM_COLOR = "#ff8d74";
const INK = "#0e1413";
const SELECTED = "#ffd466";

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

function glyph(ctx: CanvasRenderingContext2D, kind: StopKind, cx: number, cy: number) {
  ctx.fillStyle = INK;
  ctx.strokeStyle = INK;
  ctx.lineWidth = 1.6;
  ctx.lineCap = "round";
  if (kind === "tram") {
    roundedRect(ctx, cx - 5, cy - 3.5, 10, 9, 2);
    ctx.fill();
    ctx.beginPath(); ctx.moveTo(cx - 3, cy - 3.5); ctx.lineTo(cx, cy - 7); ctx.lineTo(cx + 3, cy - 3.5); ctx.stroke(); // pantograph
    ctx.fillStyle = "#fff"; ctx.fillRect(cx - 3.5, cy - 1.5, 7, 2.6); // window band
  } else if (kind === "bus") {
    roundedRect(ctx, cx - 5.5, cy - 5.5, 11, 10.5, 2.4);
    ctx.fill();
    ctx.fillStyle = "#fff"; ctx.fillRect(cx - 4, cy - 3.8, 8, 3.2); // windscreen
    ctx.fillStyle = INK; ctx.fillRect(cx - 4.2, cy + 4, 2.4, 2.2); ctx.fillRect(cx + 1.8, cy + 4, 2.4, 2.2); // wheels
  } else {
    // Both modes: two stacked bars, the shorthand for "several services".
    roundedRect(ctx, cx - 5.5, cy - 5.5, 11, 4.6, 1.6); ctx.fill();
    roundedRect(ctx, cx - 5.5, cy + 0.9, 11, 4.6, 1.6); ctx.fill();
  }
}

/** Draw one stop icon: a rounded tile in the mode colour(s) with a dark outline and a small vehicle glyph. */
export function drawStopIcon(kind: StopKind, selected: boolean): ImageData {
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
  ctx.fillStyle = selected ? SELECTED : kind === "tram" ? TRAM_COLOR : BUS_COLOR;
  ctx.fill();
  if (kind === "both" && !selected) {
    ctx.clip();
    ctx.fillStyle = TRAM_COLOR;
    ctx.beginPath(); ctx.moveTo(SIZE, 0); ctx.lineTo(SIZE, SIZE); ctx.lineTo(0, SIZE); ctx.closePath(); ctx.fill();
  }
  ctx.restore();
  roundedRect(ctx, pad, pad, size, size, 7);
  ctx.lineWidth = selected ? 3 : 2;
  ctx.strokeStyle = selected ? "#ffffff" : INK;
  ctx.stroke();
  glyph(ctx, kind, SIZE / 2, SIZE / 2 + 0.5);
  return ctx.getImageData(0, 0, SIZE * RATIO, SIZE * RATIO);
}

/** A wedge pointing up and sitting outside the vehicle dot; rotated by the bearing it shows the heading. */
export function drawHeadingWedge(): ImageData {
  const size = 96;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas is not available");
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = INK;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.beginPath(); ctx.moveTo(48, 5); ctx.lineTo(58, 21); ctx.lineTo(38, 21); ctx.closePath();
  ctx.stroke(); ctx.fill();
  return ctx.getImageData(0, 0, size, size);
}

export function stopIconId(kind: StopKind, selected = false) {
  return `stop-${kind}${selected ? "-selected" : ""}`;
}

export function registerIcons(map: MapLibreMap) {
  for (const kind of STOP_KINDS) {
    for (const selected of [false, true]) {
      const id = stopIconId(kind, selected);
      if (!map.hasImage(id)) map.addImage(id, drawStopIcon(kind, selected), { pixelRatio: RATIO });
    }
  }
  if (!map.hasImage("heading")) map.addImage("heading", drawHeadingWedge(), { pixelRatio: 2 });
}
