import { useEffect, useRef } from "react";
import { AttributionControl, Map as MapLibreMap, NavigationControl, setWorkerUrl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { basemapStyle } from "./basemap";
import { BUS_COLOR, registerIcons, TRAM_COLOR } from "./stopIcons";
import { anyMoved, interpolate, positions } from "./vehicles";
import type { FeatureCollection, LineDetail, StopSummary } from "./types";

setWorkerUrl(mapWorkerUrl);

const MODE_COLOR = ["match", ["get", "mode"], "tram", TRAM_COLOR, BUS_COLOR] as const;
// Routes are context, not content: quiet until the map is zoomed in, and hairlines at city scale.
const ROUTE_COLOR = ["match", ["get", "mode"], "tram", "#c9705c", "#4f9f87"] as const;
// Same limits as the punctuality view: early below -60 s, late above 180 s.
const DELAY_COLOR = [
  "case",
  ["==", ["get", "delay_seconds"], null], "#9aa69f",
  [">", ["get", "delay_seconds"], 180], "#ffa24d",
  ["<", ["get", "delay_seconds"], -60], "#8ea2ff",
  "#3fd69b",
] as const;
const INK = "#0e1413";
const HALO = "#0e1413";
const TEXT = "#e9eee9";
const FONT_BOLD = ["Noto Sans Bold"];
const FONT = ["Noto Sans Regular"];

/** Stops are hidden below this zoom; the route lines carry the overview. */
export const STOP_MIN_ZOOM = 13.5;
const TWEEN_MS = 1_200;
const TWEEN_MAX_VEHICLES = 150;

export interface MapRequest<T> { value: T; nonce: number }
export type VehicleLook = "dense" | "focus";

interface MapCanvasProps {
  network: FeatureCollection | null;
  vehicles: FeatureCollection | null;
  vehicleLook: VehicleLook;
  showBuses: boolean;
  showTrams: boolean;
  focusLine: LineDetail | null;
  picked: StopSummary[];
  selectedVehicleId: string | null;
  fit: MapRequest<[[number, number], [number, number]]> | null;
  flyTo: MapRequest<[number, number]> | null;
  onStop: (id: string, name: string, coordinate: [number, number]) => void;
  onLine: (routeId: string, label: string) => void;
  onVehicle: (vehicleId: string) => void;
  onZoomedOut: (zoomedOut: boolean) => void;
  label: string;
}

const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Space the bottom sheet covers on a phone, so fitted and centred content stays visible above it. */
function sheetInset() {
  return window.innerWidth <= 820 ? Math.round(window.innerHeight * 0.46) : 0;
}

export default function MapCanvas(props: MapCanvasProps) {
  const { network, vehicles, vehicleLook, showBuses, showTrams, focusLine, picked, selectedVehicleId, fit, flyTo } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const latest = useRef(props);
  latest.current = props;
  const stopModes = useRef(new Map<string, string>());
  const rendered = useRef<Map<string, [number, number]>>(new Map());
  const tween = useRef(0);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new MapLibreMap({
      container: containerRef.current,
      style: basemapStyle,
      center: [21.013, 52.228],
      zoom: 12,
      minZoom: 9,
      maxZoom: 18,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new AttributionControl(), "bottom-right");
    let zoomedOut = map.getZoom() < STOP_MIN_ZOOM;
    map.on("zoom", () => {
      const now = map.getZoom() < STOP_MIN_ZOOM;
      if (now !== zoomedOut) { zoomedOut = now; latest.current.onZoomedOut(now); }
    });
    map.on("load", () => {
      registerIcons(map);
      map.addSource("network", { type: "geojson", data: empty });
      map.addSource("vehicles", { type: "geojson", data: empty });
      map.addSource("focus", { type: "geojson", data: empty });
      map.addSource("picked", { type: "geojson", data: empty });

      map.addLayer({
        id: "routes", type: "line", source: "network",
        filter: ["==", ["get", "entity_type"], "route"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ROUTE_COLOR as never,
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 14, 1.8, 17, 4],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0.1, 13, 0.22, 15, 0.36],
        },
      });
      map.addLayer({
        id: "focus-line-casing", type: "line", source: "focus", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": INK, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 15, 12] },
      });
      map.addLayer({
        id: "focus-line", type: "line", source: "focus", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": MODE_COLOR as never, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 7] },
      });
      map.addLayer({
        id: "stops", type: "symbol", source: "network", minzoom: STOP_MIN_ZOOM,
        filter: ["==", ["get", "entity_type"], "stop"],
        layout: {
          "icon-image": ["concat", "stop-", ["get", "mode"]],
          "icon-size": ["interpolate", ["linear"], ["zoom"], 13.5, 0.62, 15, 0.85, 17, 1.08, 18, 1.2],
          "symbol-sort-key": ["match", ["get", "mode"], "both", 0, "tram", 1, 2],
          "icon-allow-overlap": false,
        },
      });
      map.addLayer({
        id: "stop-names", type: "symbol", source: "network", minzoom: 16,
        filter: ["==", ["get", "entity_type"], "stop"],
        layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": 12, "text-anchor": "top", "text-offset": [0, 1.1], "text-max-width": 8, "text-optional": true },
        paint: { "text-color": TEXT, "text-halo-color": HALO, "text-halo-width": 1.6 },
      });
      map.addLayer({
        id: "focus-stops", type: "symbol", source: "focus", minzoom: 11.5, filter: ["==", ["geometry-type"], "Point"],
        layout: {
          "icon-image": ["concat", "stop-", ["get", "mode"]],
          "icon-size": ["interpolate", ["linear"], ["zoom"], 11.5, 0.45, 14, 0.7, 17, 1],
          "icon-allow-overlap": false,
        },
      });
      map.addLayer({
        id: "focus-stop-names", type: "symbol", source: "focus", minzoom: 14.5, filter: ["==", ["geometry-type"], "Point"],
        layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": 12, "text-anchor": "top", "text-offset": [0, 1.1], "text-max-width": 8, "text-optional": true },
        paint: { "text-color": TEXT, "text-halo-color": HALO, "text-halo-width": 1.6 },
      });
      map.addLayer({
        id: "picked-stops", type: "symbol", source: "picked", filter: ["==", ["get", "kind"], "platform"],
        layout: { "icon-image": ["concat", "stop-", ["get", "mode"], "-selected"], "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 0.3, 13, 0.55, 15, 0.95, 18, 1.2], "icon-allow-overlap": true },
      });
      map.addLayer({
        id: "picked-names", type: "symbol", source: "picked", filter: ["==", ["get", "kind"], "label"],
        layout: { "text-field": ["get", "name"], "text-font": FONT_BOLD, "text-size": 14, "text-anchor": "bottom", "text-offset": [0, -1.2], "text-max-width": 9, "text-allow-overlap": true },
        paint: { "text-color": "#fff2c9", "text-halo-color": HALO, "text-halo-width": 2.2 },
      });
      map.addLayer({
        id: "vehicle-heading", type: "symbol", source: "vehicles",
        filter: ["==", ["typeof", ["get", "bearing"]], "number"],
        layout: {
          "icon-image": "heading", "icon-rotate": ["get", "bearing"], "icon-rotation-alignment": "map", "icon-pitch-alignment": "map",
          "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 15, 1], "icon-allow-overlap": true, "icon-ignore-placement": true,
        },
      });
      map.addLayer({
        id: "vehicles", type: "circle", source: "vehicles",
        paint: {
          "circle-color": MODE_COLOR as never,
          "circle-stroke-color": DELAY_COLOR as never,
          "circle-opacity": ["case", ["boolean", ["get", "is_stale"], false], 0.45, 1],
          "circle-stroke-opacity": ["case", ["boolean", ["get", "is_stale"], false], 0.5, 1],
        },
      });
      map.addLayer({
        id: "vehicle-labels", type: "symbol", source: "vehicles",
        layout: { "text-field": ["get", "line"], "text-font": FONT_BOLD },
      });
      map.addLayer({
        id: "vehicle-selected", type: "circle", source: "vehicles", filter: ["==", ["get", "id"], ""],
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 12, 15, 19], "circle-color": "rgba(255,255,255,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 },
      });
      ready.current = true;
      syncAll(map, latest.current, stopModes.current);

      const hit = (event: MapMouseEvent, layers: string[]) => {
        const pad = 8;
        return map.queryRenderedFeatures([[event.point.x - pad, event.point.y - pad], [event.point.x + pad, event.point.y + pad]], { layers: layers.filter((id) => map.getLayer(id)) });
      };
      map.on("click", (event: MapMouseEvent) => {
        const handlers = latest.current;
        const vehicle = hit(event, ["vehicles"])[0];
        if (vehicle) { handlers.onVehicle(String(vehicle.properties?.id)); return; }
        const stop = hit(event, ["picked-stops", "stops", "focus-stops"])[0];
        if (stop && stop.geometry.type === "Point") {
          handlers.onStop(String(stop.properties?.id ?? stop.id), String(stop.properties?.name), stop.geometry.coordinates as [number, number]);
          return;
        }
        const route = hit(event, ["routes"])[0];
        if (route) handlers.onLine(String(route.properties?.route_id), String(route.properties?.short_name));
      });
      const interactive = ["vehicles", "stops", "focus-stops", "picked-stops", "routes"];
      map.on("mousemove", (event) => { map.getCanvas().style.cursor = hit(event, interactive).length ? "pointer" : ""; });
    });
    mapRef.current = map;
    return () => { ready.current = false; cancelAnimationFrame(tween.current); map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    setNetwork(map, network, stopModes.current);
    syncPicked(map, picked, stopModes.current);
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- runs on the request nonce / data identity only
  }, [network]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready.current) return;
    cancelAnimationFrame(tween.current);
    const source = map.getSource("vehicles") as GeoJSONSource | undefined;
    if (!source) return;
    const target = vehicles ?? { type: "FeatureCollection" as const, features: [], data_mode: "live" as const, observed_at: null, notice: "" };
    const from = rendered.current;
    // Polls repeat the same positions until the feed updates (about every two minutes), so only
    // animate when something actually moved; otherwise a single update is enough.
    const smooth = target.data_mode === "live" && target.features.length > 0 && target.features.length <= TWEEN_MAX_VEHICLES && from.size > 0 && anyMoved(from, target);
    if (!smooth) { source.setData(withIds(target)); rendered.current = positions(target); return; }
    const started = performance.now();
    const step = (now: number) => {
      const progress = (now - started) / TWEEN_MS;
      const frame = interpolate(from, target, progress);
      source.setData(withIds(frame));
      rendered.current = positions(frame);
      if (progress < 1) tween.current = requestAnimationFrame(step);
    };
    tween.current = requestAnimationFrame(step);
  }, [vehicles]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) syncFocus(map, focusLine); }, [focusLine]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) syncPicked(map, picked, stopModes.current); }, [picked]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) applyFilters(map, showBuses, showTrams, selectedVehicleId); }, [showBuses, showTrams, selectedVehicleId]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) applyVehicleLook(map, vehicleLook); }, [vehicleLook]);
  useEffect(() => {
    const map = mapRef.current;
    if (map && fit) map.fitBounds(fit.value, { padding: { top: 90, bottom: 70 + sheetInset(), left: 50, right: 50 }, maxZoom: 15, duration: 800 });
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- runs on the request nonce / data identity only
  }, [fit?.nonce]);
  useEffect(() => {
    const map = mapRef.current;
    if (map && flyTo) map.easeTo({ center: flyTo.value, zoom: Math.max(map.getZoom(), 15), duration: 700, offset: [0, -sheetInset() / 2] });
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- runs on the request nonce / data identity only
  }, [flyTo?.nonce]);

  return <div className="map-wrap"><div ref={containerRef} className="map-canvas" role="application" aria-label={props.label} /></div>;
}

function withIds(collection: FeatureCollection): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: collection.features.map((feature) => ({
      type: "Feature" as const,
      id: feature.id,
      geometry: feature.geometry,
      properties: { ...feature.properties, id: feature.id },
    })),
  };
}

function setNetwork(map: MapLibreMap, network: FeatureCollection | null, modes: Map<string, string>) {
  const source = map.getSource("network") as GeoJSONSource | undefined;
  if (!source || !network) return;
  modes.clear();
  for (const feature of network.features) {
    if (feature.properties.entity_type === "stop") modes.set(feature.id, String(feature.properties.mode ?? "bus"));
  }
  const data = withIds(network);
  // An API without stop modes (an older backend) still gets drawn: unknown means bus.
  for (const feature of data.features) {
    if (feature.properties?.entity_type === "stop" && !feature.properties.mode) feature.properties.mode = "bus";
  }
  source.setData(data);
}

function syncFocus(map: MapLibreMap, line: LineDetail | null) {
  const source = map.getSource("focus") as GeoJSONSource | undefined;
  if (!source) return;
  const features: GeoJSON.Feature[] = [];
  if (line?.shape) {
    const geometry: GeoJSON.Geometry = line.shape.type === "LineString"
      ? { type: "MultiLineString", coordinates: [line.shape.coordinates] }
      : line.shape;
    features.push({ type: "Feature", geometry, properties: { mode: line.mode } });
    line.stops.forEach((stop) => features.push({
      type: "Feature",
      id: stop.id,
      geometry: { type: "Point", coordinates: [stop.longitude, stop.latitude] },
      properties: { id: stop.id, name: stop.name, mode: line.mode },
    }));
  }
  source.setData({ type: "FeatureCollection", features });
  // With a line in focus the other routes and the generic stops step back.
  const focused = line !== null;
  if (map.getLayer("routes")) map.setPaintProperty("routes", "line-opacity", focused ? 0.07 : ["interpolate", ["linear"], ["zoom"], 10, 0.1, 13, 0.22, 15, 0.36]);
  for (const id of ["stops", "stop-names"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", focused ? "none" : "visible");
  }
}

function syncPicked(map: MapLibreMap, stops: StopSummary[], modes: Map<string, string>) {
  const source = map.getSource("picked") as GeoJSONSource | undefined;
  const features: GeoJSON.Feature[] = stops.map((stop) => ({
    type: "Feature" as const,
    id: stop.id,
    geometry: { type: "Point" as const, coordinates: [stop.longitude, stop.latitude] },
    properties: { id: stop.id, name: stop.name, kind: "platform", mode: modes.get(stop.id) ?? "both" },
  }));
  // One name for the whole station, placed above its platforms, instead of one label per platform.
  if (stops.length > 0) {
    const north = Math.max(...stops.map((stop) => stop.latitude));
    const east = stops.reduce((sum, stop) => sum + stop.longitude, 0) / stops.length;
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: [east, north] }, properties: { name: stops[0].name, kind: "label" } });
  }
  source?.setData({ type: "FeatureCollection", features });
}

function applyFilters(map: MapLibreMap, showBuses: boolean, showTrams: boolean, selectedVehicleId: string | null) {
  if (!map.getLayer("routes") || !map.getLayer("vehicles")) return;
  const modes = [...(showBuses ? ["bus"] : []), ...(showTrams ? ["tram"] : [])];
  const byMode = ["in", ["get", "mode"], ["literal", modes]] as never;
  const stopModes = [...(showBuses ? ["bus", "both"] : []), ...(showTrams ? ["tram", "both"] : [])];
  const byStopMode = ["in", ["get", "mode"], ["literal", stopModes]] as never;
  map.setFilter("routes", ["all", ["==", ["get", "entity_type"], "route"], byMode]);
  map.setFilter("stops", ["all", ["==", ["get", "entity_type"], "stop"], byStopMode]);
  map.setFilter("stop-names", ["all", ["==", ["get", "entity_type"], "stop"], byStopMode]);
  map.setFilter("vehicles", byMode);
  map.setFilter("vehicle-labels", byMode);
  map.setFilter("vehicle-heading", ["all", ["==", ["typeof", ["get", "bearing"]], "number"], byMode]);
  map.setFilter("vehicle-selected", ["==", ["get", "id"], selectedVehicleId ?? ""]);
}

/**
 * Many vehicles (the whole city) are small unlabelled dots with names only when zoomed in;
 * a single line's vehicles are large, carry their line number and show where they head.
 */
function applyVehicleLook(map: MapLibreMap, look: VehicleLook) {
  if (!map.getLayer("vehicles")) return;
  const focus = look === "focus";
  map.setPaintProperty("vehicles", "circle-radius", focus
    ? ["interpolate", ["linear"], ["zoom"], 10, 7, 15, 12]
    : ["interpolate", ["linear"], ["zoom"], 10, 2.2, 13, 3.4, 16, 6.5]);
  map.setPaintProperty("vehicles", "circle-stroke-width", focus ? ["interpolate", ["linear"], ["zoom"], 10, 2, 15, 3] : ["interpolate", ["linear"], ["zoom"], 10, 0, 13, 1, 16, 2]);
  map.setLayoutProperty("vehicle-heading", "visibility", focus ? "visible" : "none");
  map.setLayerZoomRange("vehicle-labels", focus ? 0 : 14.5, 24);
  map.setLayoutProperty("vehicle-labels", "text-size", focus ? ["interpolate", ["linear"], ["zoom"], 10, 9, 15, 12] : 10);
  map.setLayoutProperty("vehicle-labels", "text-offset", focus ? [0, 0] : [0, 1.2]);
  map.setLayoutProperty("vehicle-labels", "text-allow-overlap", focus);
  map.setLayoutProperty("vehicle-labels", "text-optional", true);
  map.setPaintProperty("vehicle-labels", "text-color", focus ? INK : TEXT);
  map.setPaintProperty("vehicle-labels", "text-halo-color", HALO);
  map.setPaintProperty("vehicle-labels", "text-halo-width", focus ? 0 : 1.6);
}

function syncAll(map: MapLibreMap, props: MapCanvasProps, modes: Map<string, string>) {
  setNetwork(map, props.network, modes);
  const source = map.getSource("vehicles") as GeoJSONSource | undefined;
  if (props.vehicles) source?.setData(withIds(props.vehicles));
  syncFocus(map, props.focusLine);
  syncPicked(map, props.picked, modes);
  applyFilters(map, props.showBuses, props.showTrams, props.selectedVehicleId);
  applyVehicleLook(map, props.vehicleLook);
}
