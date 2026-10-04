import { useEffect, useRef } from "react";
import { AttributionControl, Map as MapLibreMap, NavigationControl, setWorkerUrl, type GeoJSONSource, type MapMouseEvent } from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import type { FeatureCollection, Selection } from "./types";
import { translate } from "./i18n";
import type { Language } from "./types";

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

interface MapCanvasProps {
  network: FeatureCollection | null;
  vehicles: FeatureCollection | null;
  language: Language;
  showBuses: boolean;
  showTrams: boolean;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
}

function selectionFromFeature(properties: Record<string, unknown>, coordinate: [number, number]): Selection | null {
  if (properties.entity_type === "stop") {
    return { type: "stop", id: String(properties.id), label: String(properties.name), coordinate };
  }
  if (properties.entity_type === "route") {
    return {
      type: "route",
      id: String(properties.route_id),
      label: `${String(properties.short_name)} · ${String(properties.long_name)}`,
      coordinate,
    };
  }
  return null;
}

export default function MapCanvas({
  network, vehicles, language, showBuses, showTrams, selection, onSelect,
}: MapCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const onSelectRef = useRef(onSelect);
  const networkRef = useRef(network);
  const vehiclesRef = useRef(vehicles);
  const visibilityRef = useRef({ showBuses, showTrams });

  useEffect(() => { onSelectRef.current = onSelect; }, [onSelect]);
  useEffect(() => { networkRef.current = network; }, [network]);
  useEffect(() => { vehiclesRef.current = vehicles; }, [vehicles]);
  useEffect(() => { visibilityRef.current = { showBuses, showTrams }; }, [showBuses, showTrams]);

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
      map.addSource("network", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addSource("vehicles", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "routes",
        type: "line",
        source: "network",
        filter: ["==", ["get", "entity_type"], "route"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["match", ["get", "mode"], "tram", "#ff765e", "#81dbc0"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 5],
          "line-opacity": 0.84,
        },
      });
      map.addLayer({
        id: "stops",
        type: "circle",
        source: "network",
        filter: ["==", ["get", "entity_type"], "stop"],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2, 14, 5],
          "circle-color": "#f3ebd7",
          "circle-stroke-color": "#20332f",
          "circle-stroke-width": 1.4,
        },
      });
      map.addLayer({
        id: "vehicles",
        type: "circle",
        source: "vehicles",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 5, 14, 9],
          "circle-color": ["match", ["get", "mode"], "tram", "#ff765e", "#81dbc0"],
          "circle-stroke-color": "#172321",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "vehicle-labels",
        type: "symbol",
        source: "vehicles",
        layout: {
          "text-field": ["get", "line"],
          "text-font": ["Open Sans Bold", "Arial Unicode MS Bold"],
          "text-size": 10,
          "text-offset": [0, 1.25],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#f3ebd7", "text-halo-color": "#182321", "text-halo-width": 1.2 },
      });
      syncCollection(map, "network", networkRef.current);
      syncCollection(map, "vehicles", vehiclesRef.current);
      applyVisibility(map, visibilityRef.current);
      map.on("click", ["stops", "routes"], (event: MapMouseEvent) => {
        const feature = map.queryRenderedFeatures(event.point, { layers: ["stops", "routes"] })[0];
        if (!feature) return;
        const coordinates = (feature.geometry.type === "Point"
          ? feature.geometry.coordinates
          : event.lngLat.toArray()) as [number, number];
        const properties = { ...(feature.properties ?? {}), id: feature.id };
        const next = selectionFromFeature(properties, coordinates);
        if (next) onSelectRef.current(next);
      });
      map.on("mouseenter", ["stops", "routes"], () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", ["stops", "routes"], () => { map.getCanvas().style.cursor = ""; });
    });
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !network || !map.isStyleLoaded()) return;
    syncCollection(map, "network", network);
  }, [network]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !vehicles || !map.isStyleLoaded()) return;
    syncCollection(map, "vehicles", vehicles);
  }, [vehicles]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    applyVisibility(map, { showBuses, showTrams });
  }, [showBuses, showTrams]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selection || !map.isStyleLoaded()) return;
    map.easeTo({ center: selection.coordinate, duration: 700, offset: [-120, 0] });
  }, [selection]);

  return (
    <div className="map-wrap" aria-label={translate(language, "mapTitle")}>
      <div className="map-grid" aria-hidden="true" />
      <div ref={containerRef} className="map-canvas" />
      <div className="map-attribution-note">© OpenStreetMap contributors</div>
    </div>
  );
}

function syncCollection(map: MapLibreMap, sourceId: string, collection: FeatureCollection | null) {
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

function applyVisibility(map: MapLibreMap, visibility: { showBuses: boolean; showTrams: boolean }) {
  if (!map.getLayer("routes") || !map.getLayer("vehicles")) return;
  const modes = [
    ...(visibility.showBuses ? ["bus"] : []),
    ...(visibility.showTrams ? ["tram"] : []),
  ];
  map.setFilter("routes", ["all", ["==", ["get", "entity_type"], "route"], [
    "in", ["get", "mode"], ["literal", modes],
  ]]);
  map.setFilter("vehicles", ["in", ["get", "mode"], ["literal", modes]]);
}
