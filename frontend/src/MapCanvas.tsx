import { useEffect, useRef } from "react";
import { AttributionControl, Map as MapLibreMap, NavigationControl, setWorkerUrl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { FeatureCollection, Language, LineDetail, StopSummary } from "./types";
import { translate } from "./i18n";

const mapStyle = {
  version: 8 as const,
  glyphs: "https://fonts.openmaptiles.org/{fontstack}/{range}.pbf",
  sources: {
    osm: {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm-base", type: "raster" as const, source: "osm" }],
};

setWorkerUrl(mapWorkerUrl);

const MODE_COLOR = ["match", ["get", "mode"], "tram", "#ff765e", "#81dbc0"] as const;
// Same limits as the punctuality view: early below -60 s, late above 180 s.
const DELAY_COLOR = [
  "case",
  ["==", ["get", "delay_seconds"], null], "#8a968e",
  [">", ["get", "delay_seconds"], 180], "#ff9a3c",
  ["<", ["get", "delay_seconds"], -60], "#6a83e6",
  "#2fd08f",
] as const;

export interface MapRequest<T> { value: T; nonce: number }

interface MapCanvasProps {
  network: FeatureCollection | null;
  vehicles: FeatureCollection | null;
  language: Language;
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
}

const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export default function MapCanvas(props: MapCanvasProps) {
  const { network, vehicles, language, showBuses, showTrams, focusLine, picked, selectedVehicleId, fit, flyTo } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const latest = useRef(props);
  latest.current = props;

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new MapLibreMap({
      container: containerRef.current,
      style: mapStyle,
      center: [21.013, 52.228],
      zoom: 12,
      minZoom: 10,
      maxZoom: 18,
      attributionControl: false,
      cooperativeGestures: true,
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      map.addSource("network", { type: "geojson", data: empty, cluster: true, clusterRadius: 42, clusterMaxZoom: 14 });
      map.addSource("vehicles", { type: "geojson", data: empty });
      map.addSource("focus", { type: "geojson", data: empty });
      map.addSource("picked", { type: "geojson", data: empty });
      map.addLayer({
        id: "routes", type: "line", source: "network",
        filter: ["==", ["get", "entity_type"], "route"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": MODE_COLOR as never, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 5], "line-opacity": 0.84 },
      });
      map.addLayer({
        id: "focus-line-casing", type: "line", source: "focus", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#101716", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 14, 12] },
      });
      map.addLayer({
        id: "focus-line", type: "line", source: "focus", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": MODE_COLOR as never, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 3.5, 14, 7.5] },
      });
      map.addLayer({
        id: "stop-clusters", type: "circle", source: "network", filter: ["has", "point_count"],
        paint: { "circle-color": "#233c33", "circle-radius": ["step", ["get", "point_count"], 13, 100, 17, 500, 21], "circle-stroke-color": "#8cdec1", "circle-stroke-width": 1.5 },
      });
      map.addLayer({
        id: "stop-cluster-count", type: "symbol", source: "network", filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Open Sans Bold"], "text-size": 10 },
        paint: { "text-color": "#e8ece5" },
      });
      map.addLayer({
        id: "stops", type: "circle", source: "network",
        filter: ["all", ["==", ["get", "entity_type"], "stop"], ["!", ["has", "point_count"]]],
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 5], "circle-color": "#f3ebd7", "circle-stroke-color": "#20332f", "circle-stroke-width": 1.4 },
      });
      map.addLayer({
        id: "focus-stops", type: "circle", source: "focus", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 14, 5.5], "circle-color": "#f3ebd7", "circle-stroke-color": "#101716", "circle-stroke-width": 1.6 },
      });
      map.addLayer({
        id: "picked-stops", type: "circle", source: "picked",
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 7, 14, 11], "circle-color": "#ffd86b", "circle-stroke-color": "#101716", "circle-stroke-width": 2.2 },
      });
      map.addLayer({
        id: "vehicles", type: "circle", source: "vehicles",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 5, 14, 9],
          "circle-color": MODE_COLOR as never,
          "circle-stroke-color": DELAY_COLOR as never,
          "circle-stroke-width": 2.6,
          "circle-opacity": ["case", ["boolean", ["get", "is_stale"], false], 0.42, 1],
          "circle-stroke-opacity": ["case", ["boolean", ["get", "is_stale"], false], 0.48, 1],
        },
      });
      map.addLayer({
        id: "vehicle-labels", type: "symbol", source: "vehicles",
        layout: { "text-field": ["get", "line"], "text-font": ["Open Sans Bold"], "text-size": 10, "text-offset": [0, 1.35], "text-allow-overlap": true },
        paint: { "text-color": "#f3ebd7", "text-halo-color": "#182321", "text-halo-width": 1.2 },
      });
      map.addLayer({
        id: "vehicle-selected", type: "circle", source: "vehicles", filter: ["==", ["get", "id"], ""],
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 11, 14, 16], "circle-color": "rgba(255,255,255,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 },
      });
      ready.current = true;
      syncAll(map, latest.current);

      const hit = (event: MapMouseEvent, layers: string[]) => {
        const pad = 7;
        return map.queryRenderedFeatures([[event.point.x - pad, event.point.y - pad], [event.point.x + pad, event.point.y + pad]], { layers: layers.filter((id) => map.getLayer(id)) });
      };
      map.on("click", async (event: MapMouseEvent) => {
        const handlers = latest.current;
        const vehicle = hit(event, ["vehicles"])[0];
        if (vehicle) { handlers.onVehicle(String(vehicle.properties?.id)); return; }
        const cluster = hit(event, ["stop-clusters"])[0];
        const source = map.getSource("network") as GeoJSONSource | undefined;
        if (cluster && source) {
          const zoom = await source.getClusterExpansionZoom(Number(cluster.properties?.cluster_id));
          if (cluster.geometry.type === "Point") map.easeTo({ center: cluster.geometry.coordinates as [number, number], zoom });
          return;
        }
        const stop = hit(event, ["stops", "focus-stops"])[0];
        if (stop && stop.geometry.type === "Point") {
          handlers.onStop(String(stop.properties?.id ?? stop.id), String(stop.properties?.name), stop.geometry.coordinates as [number, number]);
          return;
        }
        const route = hit(event, ["routes"])[0];
        if (route) handlers.onLine(String(route.properties?.route_id), String(route.properties?.short_name));
      });
      const interactive = ["vehicles", "stops", "focus-stops", "stop-clusters", "routes"];
      map.on("mousemove", (event) => { map.getCanvas().style.cursor = hit(event, interactive).length ? "pointer" : ""; });
    });
    mapRef.current = map;
    return () => { ready.current = false; map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => { const map = mapRef.current; if (map && ready.current) setData(map, "network", network); }, [network]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) setData(map, "vehicles", vehicles); }, [vehicles]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) syncFocus(map, focusLine); }, [focusLine]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) syncPicked(map, picked); }, [picked]);
  useEffect(() => { const map = mapRef.current; if (map && ready.current) applyFilters(map, showBuses, showTrams, selectedVehicleId); }, [showBuses, showTrams, selectedVehicleId]);
  useEffect(() => {
    const map = mapRef.current;
    if (map && fit) map.fitBounds(fit.value, { padding: { top: 70, bottom: 70, left: 50, right: 50 }, maxZoom: 15, duration: 800 });
  }, [fit?.nonce]);
  useEffect(() => {
    const map = mapRef.current;
    if (map && flyTo) map.easeTo({ center: flyTo.value, zoom: Math.max(map.getZoom(), 15), duration: 700 });
  }, [flyTo?.nonce]);

  return (
    <div className="map-wrap" aria-label={translate(language, "mapTitle")}>
      <div className="map-grid" aria-hidden="true" />
      <div ref={containerRef} className="map-canvas" />
      <div className="map-attribution-note">© OpenStreetMap contributors</div>
    </div>
  );
}

function setData(map: MapLibreMap, sourceId: string, collection: FeatureCollection | null) {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined;
  if (!source || !collection) return;
  source.setData({
    type: "FeatureCollection",
    features: collection.features.map((feature) => ({
      type: "Feature" as const,
      id: feature.id,
      geometry: feature.geometry,
      properties: { ...feature.properties, id: feature.id },
    })),
  });
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
  if (map.getLayer("routes")) map.setPaintProperty("routes", "line-opacity", focused ? 0.13 : 0.84);
  for (const id of ["stops", "stop-clusters", "stop-cluster-count"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", focused ? "none" : "visible");
  }
}

function syncPicked(map: MapLibreMap, stops: StopSummary[]) {
  const source = map.getSource("picked") as GeoJSONSource | undefined;
  source?.setData({
    type: "FeatureCollection",
    features: stops.map((stop) => ({
      type: "Feature" as const,
      id: stop.id,
      geometry: { type: "Point" as const, coordinates: [stop.longitude, stop.latitude] },
      properties: { id: stop.id, name: stop.name },
    })),
  });
}

function applyFilters(map: MapLibreMap, showBuses: boolean, showTrams: boolean, selectedVehicleId: string | null) {
  if (!map.getLayer("routes") || !map.getLayer("vehicles")) return;
  const modes = [...(showBuses ? ["bus"] : []), ...(showTrams ? ["tram"] : [])];
  const byMode: never = ["in", ["get", "mode"], ["literal", modes]] as never;
  map.setFilter("routes", ["all", ["==", ["get", "entity_type"], "route"], byMode]);
  map.setFilter("vehicles", byMode);
  map.setFilter("vehicle-labels", byMode);
  map.setFilter("vehicle-selected", ["==", ["get", "id"], selectedVehicleId ?? ""]);
}

function syncAll(map: MapLibreMap, props: MapCanvasProps) {
  setData(map, "network", props.network);
  setData(map, "vehicles", props.vehicles);
  syncFocus(map, props.focusLine);
  syncPicked(map, props.picked);
  applyFilters(map, props.showBuses, props.showTrams, props.selectedVehicleId);
}
