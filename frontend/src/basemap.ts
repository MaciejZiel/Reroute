import type { LayerSpecification, StyleSpecification } from "maplibre-gl";

/**
 * A calm dark basemap drawn from OpenFreeMap's public vector tiles (OpenMapTiles schema).
 * OpenFreeMap needs no API key and has no request limits; attribution is required and is
 * shown by the map's attribution control. Roads, water and parks are kept as quiet shapes so
 * the transit layers carry the contrast; there are no POI icons and street names appear only
 * when zoomed in far.
 */
export const BASEMAP = {
  background: "#111918",
  water: "#0a1217",
  park: "#14211d",
  building: "#1a2523",
  roadMinor: "#27332f",
  roadMajor: "#33423d",
  roadMotorway: "#364642",
  label: "#8d9a93",
  labelStrong: "#b4bfb8",
} as const;

const FONT_REGULAR = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];

const road = (id: string, classes: string[], color: string, widths: [number, number, number], minzoom = 0): LayerSpecification => ({
  id, type: "line", source: "tiles", "source-layer": "transportation", minzoom,
  filter: ["all", ["in", ["get", "class"], ["literal", classes]], ["!=", ["get", "brunnel"], "tunnel"]],
  layout: { "line-cap": "round", "line-join": "round" },
  paint: { "line-color": color, "line-width": ["interpolate", ["exponential", 1.4], ["zoom"], 10, widths[0], 14, widths[1], 18, widths[2]] },
});

const layers: LayerSpecification[] = [
  { id: "background", type: "background", paint: { "background-color": BASEMAP.background } },
  { id: "park", type: "fill", source: "tiles", "source-layer": "park", paint: { "fill-color": BASEMAP.park } },
  { id: "grass", type: "fill", source: "tiles", "source-layer": "landcover", filter: ["in", ["get", "class"], ["literal", ["grass", "wood"]]], paint: { "fill-color": BASEMAP.park, "fill-opacity": 0.7 } },
  { id: "water", type: "fill", source: "tiles", "source-layer": "water", paint: { "fill-color": BASEMAP.water } },
  { id: "waterway", type: "line", source: "tiles", "source-layer": "waterway", paint: { "line-color": BASEMAP.water, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 16, 3] } },
  { id: "building", type: "fill", source: "tiles", "source-layer": "building", minzoom: 14.5, paint: { "fill-color": BASEMAP.building, "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14.5, 0, 16, 1] } },
  road("road-minor", ["minor", "service", "track"], BASEMAP.roadMinor, [0.3, 0.8, 5], 12.5),
  road("road-tertiary", ["tertiary"], BASEMAP.roadMinor, [0.5, 1.4, 8], 11),
  road("road-secondary", ["secondary", "primary"], BASEMAP.roadMajor, [0.7, 2, 11]),
  road("road-trunk", ["trunk", "motorway"], BASEMAP.roadMotorway, [0.9, 2.4, 12]),
  {
    id: "road-names", type: "symbol", source: "tiles", "source-layer": "transportation_name", minzoom: 15.5,
    layout: { "symbol-placement": "line", "text-field": ["coalesce", ["get", "name:latin"], ["get", "name"]], "text-font": FONT_REGULAR, "text-size": 11, "text-letter-spacing": 0.02 },
    paint: { "text-color": BASEMAP.label, "text-halo-color": BASEMAP.background, "text-halo-width": 1.4 },
  },
  {
    id: "place-district", type: "symbol", source: "tiles", "source-layer": "place", minzoom: 11.5, maxzoom: 15,
    filter: ["in", ["get", "class"], ["literal", ["suburb", "neighbourhood", "quarter"]]],
    layout: { "text-field": ["coalesce", ["get", "name:latin"], ["get", "name"]], "text-font": FONT_REGULAR, "text-size": ["interpolate", ["linear"], ["zoom"], 11.5, 11, 14, 13], "text-transform": "uppercase", "text-letter-spacing": 0.1, "text-max-width": 8 },
    paint: { "text-color": BASEMAP.label, "text-halo-color": BASEMAP.background, "text-halo-width": 1.4 },
  },
  {
    id: "place-city", type: "symbol", source: "tiles", "source-layer": "place", maxzoom: 11.5,
    filter: ["in", ["get", "class"], ["literal", ["city", "town"]]],
    layout: { "text-field": ["coalesce", ["get", "name:latin"], ["get", "name"]], "text-font": FONT_BOLD, "text-size": 14 },
    paint: { "text-color": BASEMAP.labelStrong, "text-halo-color": BASEMAP.background, "text-halo-width": 1.6 },
  },
];

export const basemapStyle: StyleSpecification = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {
    tiles: {
      type: "vector",
      url: "https://tiles.openfreemap.org/planet",
      attribution:
        '<a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a> <a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">© OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>',
    },
  },
  layers,
};
