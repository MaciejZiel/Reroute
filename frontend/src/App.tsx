import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapCanvas, { type MapRequest } from "./MapCanvas";
import MapControls from "./MapControls";
import ModelPanel from "./ModelPanel";
import Panel from "./Panel";
import Punctuality from "./Punctuality";
import SearchBox from "./SearchBox";
import Stepper from "./Stepper";
import { fetchDepartures, fetchLine, fetchLineVehicles, fetchNetwork, fetchVehicle, fetchVehicles } from "./api";
import { geometryBounds } from "./format";
import { translate, type TranslationKey } from "./i18n";
import { usePolled } from "./usePolled";
import { headingVehicleIds, scopeVehicles, type LineScope, type Step } from "./vehicles";
import type { FeatureCollection, Focus, Language, LineDepartures, StopMatch, StopSummary } from "./types";
import "./styles.css";

function RouteIcon() {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8 18h6.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H16"/></svg>;
}

const NO_FOCUS: Focus = { stopId: null, stopName: null, stopCoordinate: null, lineId: null, lineLabel: null, vehicleId: null };
type Sheet = "peek" | "half" | "full";
const SHEETS: Sheet[] = ["peek", "half", "full"];

export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("reroute-language") === "en" ? "en" : "pl");
  const [focus, setFocus] = useState<Focus>(NO_FOCUS);
  const [showAll, setShowAll] = useState(false);
  const [lineScope, setLineScope] = useState<LineScope>("all");
  const [showBuses, setShowBuses] = useState(true);
  const [showTrams, setShowTrams] = useState(true);
  const [showPunctuality, setShowPunctuality] = useState(false);
  const [showModel, setShowModel] = useState(false);
  const [zoomedOut, setZoomedOut] = useState(true);
  const [sheet, setSheet] = useState<Sheet>("peek");
  const [fit, setFit] = useState<MapRequest<[[number, number], [number, number]]> | null>(null);
  const [flyTo, setFlyTo] = useState<MapRequest<[number, number]> | null>(null);
  const [demoVehicles, setDemoVehicles] = useState<FeatureCollection | null>(null);
  const nonce = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const t = useCallback((key: TranslationKey) => translate(language, key), [language]);

  useEffect(() => { localStorage.setItem("reroute-language", language); document.documentElement.lang = language; document.title = translate(language, "documentTitle"); }, [language]);

  const [networkAttempt, setNetworkAttempt] = useState(0);
  const network = usePolled(`network|${networkAttempt}`, fetchNetwork, 10 * 60_000);
  // The whole fleet is only drawn on request, so poll it slowly in the background (it feeds the counter and status).
  const allVehicles = usePolled("vehicles", fetchVehicles, showAll ? 15_000 : 60_000);
  const stop = usePolled(focus.stopId, (signal) => fetchDepartures(focus.stopId as string, signal), 20_000);
  const line = usePolled(focus.lineId, (signal) => fetchLine(focus.lineId as string, signal), 10 * 60_000);
  const lineVehicles = usePolled(focus.lineId, (signal) => fetchLineVehicles(focus.lineId as string, signal), 15_000);
  const vehicle = usePolled(focus.vehicleId ? `${focus.vehicleId}|${language}` : null, (signal) => fetchVehicle(focus.vehicleId as string, language, signal), 15_000);

  // Zoom to the line's route when it arrives.
  useEffect(() => {
    const bounds = geometryBounds(line.data?.shape ?? null);
    if (bounds) setFit({ value: bounds, nonce: ++nonce.current });
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- runs on the request nonce / data identity only
  }, [line.data?.route_id]);

  // On a phone the panel is a bottom sheet: open it when something is picked, fold it when the selection is cleared.
  const hasFocus = Boolean(focus.stopId || focus.lineId || focus.vehicleId);
  useEffect(() => { scroller.current?.scrollTo({ top: 0 }); }, [focus.stopId, focus.lineId, focus.vehicleId]);
  useEffect(() => { setSheet(hasFocus ? "half" : "peek"); }, [focus.stopId, focus.lineId, focus.vehicleId, hasFocus]);

  // Vehicles are hidden until asked for: a picked line shows its own, the switch shows the whole city.
  const heading = useMemo(() => headingVehicleIds(stop.data, focus.lineId, focus.lineLabel), [stop.data, focus.lineId, focus.lineLabel]);
  const scope: LineScope = focus.stopId ? lineScope : "all";
  const lineShown = useMemo(() => scopeVehicles(lineVehicles.data, scope, heading, focus.vehicleId), [lineVehicles.data, scope, heading, focus.vehicleId]);
  const liveVehicles = focus.lineId ? lineShown : showAll ? allVehicles.data : null;

  // Demo vehicles get a small, clearly labelled movement so the map feels alive without a live feed.
  useEffect(() => {
    if (!liveVehicles || liveVehicles.data_mode !== "demo") { setDemoVehicles(null); return; }
    const base = liveVehicles;
    const started = Date.now();
    const timer = window.setInterval(() => {
      const phase = (Date.now() - started) / 8_000;
      setDemoVehicles({ ...base, features: base.features.map((feature, index) => {
        if (feature.geometry.type !== "Point") return feature;
        const [lng, lat] = feature.geometry.coordinates;
        const offset = phase + index * 1.7;
        return { ...feature, geometry: { ...feature.geometry, coordinates: [lng + Math.sin(offset) * 0.0015, lat + Math.cos(offset) * 0.0012] } };
      }) });
    }, 120);
    return () => window.clearInterval(timer);
  }, [liveVehicles]);
  const visibleVehicles = liveVehicles?.data_mode === "demo" ? demoVehicles ?? liveVehicles : liveVehicles;

  const networkData = network.data;
  const routeCount = useMemo(() => networkData?.features.filter((feature) => feature.properties.entity_type === "route").length ?? 0, [networkData]);
  const stopCount = useMemo(() => networkData?.features.filter((feature) => feature.properties.entity_type === "stop").length ?? 0, [networkData]);
  const vehicleCount = allVehicles.data?.features.length ?? 0;

  const pickStop = useCallback((id: string, name: string, coordinate: [number, number]) => {
    setFocus({ ...NO_FOCUS, stopId: id, stopName: name, stopCoordinate: coordinate });
    setLineScope("all");
    setFlyTo({ value: coordinate, nonce: ++nonce.current });
  }, []);
  const pickSearch = useCallback((match: StopMatch) => pickStop(match.id, match.name, [match.longitude, match.latitude]), [pickStop]);
  const pickLine = useCallback((routeId: string, label: string) => setFocus((current) => ({ ...current, lineId: routeId, lineLabel: label, vehicleId: null })), []);
  const pickDeparturesLine = useCallback((item: LineDepartures) => pickLine(item.route_id, item.line), [pickLine]);
  const pickVehicle = useCallback((vehicleId: string) => {
    setFocus((current) => {
      const feature = (current.lineId ? lineVehicles.data : allVehicles.data)?.features.find((item) => item.id === vehicleId);
      const label = feature ? String(feature.properties.line) : current.lineLabel;
      return { ...current, lineId: current.lineId ?? label, lineLabel: label, vehicleId };
    });
  }, [allVehicles.data, lineVehicles.data]);
  const back = useCallback(() => setFocus((current) => {
    if (current.vehicleId) return { ...current, vehicleId: null };
    if (current.lineId) return { ...current, lineId: null, lineLabel: null };
    return NO_FOCUS;
  }), []);
  const clear = useCallback(() => setFocus(NO_FOCUS), []);
  // A finished step of the stepper returns to the view where that step is the one to choose.
  const goStep = useCallback((step: Step) => setFocus((current) => {
    if (step === 1) return NO_FOCUS;
    if (step === 2) return { ...current, lineId: null, lineLabel: null, vehicleId: null };
    return { ...current, vehicleId: null };
  }), []);

  const apiError = network.error !== null && !networkData;
  const hasLiveVehicles = allVehicles.data?.data_mode === "live";
  const hasLiveSchedule = networkData?.data_mode === "live";
  const isLive = hasLiveVehicles || hasLiveSchedule;
  const feedLabel = hasLiveVehicles ? t("live") : hasLiveSchedule ? t("scheduleOnly") : t("demo");
  const sourceNotices = [...new Set([networkData?.notice, allVehicles.data?.notice].filter(Boolean))].join(" ");

  // Highlight the picked station right away from the click, then with all its platforms once they load.
  const picked = useMemo<StopSummary[]>(() => {
    if (stop.data?.platforms.length) return stop.data.platforms;
    if (focus.stopId && focus.stopCoordinate) return [{ id: focus.stopId, name: focus.stopName ?? "", longitude: focus.stopCoordinate[0], latitude: focus.stopCoordinate[1], platform: null }];
    return [];
  }, [stop.data, focus.stopId, focus.stopName, focus.stopCoordinate]);

  const drag = useRef<{ y: number; sheet: Sheet } | null>(null);
  const onHandleDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    drag.current = { y: event.clientY, sheet };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onHandleUp = (event: React.PointerEvent<HTMLButtonElement>) => {
    const start = drag.current;
    drag.current = null;
    if (!start) return;
    const delta = start.y - event.clientY;
    const index = SHEETS.indexOf(start.sheet);
    if (Math.abs(delta) < 10) setSheet(SHEETS[(index + 1) % SHEETS.length]);
    else setSheet(SHEETS[Math.min(SHEETS.length - 1, Math.max(0, index + (delta > 0 ? 1 : -1)))]);
  };
  // Enter and Space fire a click with no pointer behind it (detail 0); pointer taps are handled above.
  const onHandleKey = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (event.detail === 0) setSheet((current) => SHEETS[(SHEETS.indexOf(current) + 1) % SHEETS.length]);
  };

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="./" aria-label="Reroute"><span className="brand-mark"><RouteIcon /></span><span className="brand-name">reroute<span className="brand-period">.</span></span></a>
        <h1 className="tagline">{t("productTitle")}</h1>
        <div className="topbar-actions">
          <span className={`feed-status ${isLive ? "is-live" : "is-demo"}`}><span className="pulse-dot" />{feedLabel}</span>
          <button type="button" className="language-toggle" onClick={() => setLanguage(language === "pl" ? "en" : "pl")} aria-label={language === "pl" ? "Switch to English" : "Przełącz na polski"}>{language === "pl" ? "EN" : "PL"}</button>
        </div>
      </header>

      <div className="stage">
        <section className="map-stage" aria-label={t("mapTitle")}>
          <div className="map-overlay">
            <SearchBox t={t} onPick={pickSearch} />
            <MapControls t={t} showAll={showAll} onShowAll={setShowAll} allowShowAll={!focus.lineId} vehicleCount={vehicleCount}
              showBuses={showBuses} showTrams={showTrams} onBuses={setShowBuses} onTrams={setShowTrams}
              punctuality={showPunctuality} onPunctuality={() => { setShowPunctuality(!showPunctuality); setShowModel(false); }}
              model={showModel} onModel={() => { setShowModel(!showModel); setShowPunctuality(false); }} zoomedOut={zoomedOut && !focus.lineId} />
          </div>
          <MapCanvas network={networkData} vehicles={visibleVehicles} vehicleLook={focus.lineId ? "focus" : "dense"}
            showBuses={showBuses} showTrams={showTrams} focusLine={focus.lineId ? line.data : null} picked={picked}
            selectedVehicleId={focus.vehicleId} fit={fit} flyTo={flyTo} label={t("mapTitle")}
            onStop={pickStop} onLine={pickLine} onVehicle={pickVehicle} onZoomedOut={setZoomedOut} />
          {showPunctuality && <Punctuality language={language} t={t} onClose={() => setShowPunctuality(false)}/>}
          {showModel && <ModelPanel language={language} t={t} onClose={() => setShowModel(false)}/>}
          {!networkData && !apiError && <div className="map-loading" role="status"><span className="loading-ring"/>{t("loading")}</div>}
          {apiError && (
            <div className="map-error" role="alert">
              <strong>{t("networkErrorTitle")}</strong>
              <p>{t("networkErrorBody")}</p>
              <button type="button" className="retry-button" onClick={() => setNetworkAttempt((n) => n + 1)}>{t("retry")}</button>
            </div>
          )}
          <div className="map-legend" aria-label={t("legend")}>
            <span><i className="tile bus"/>{t("buses")}</span><span><i className="tile tram"/>{t("trams")}</span><span><i className="tile both"/>{t("both")}</span>
            {visibleVehicles && <span className="legend-delay">{t("legendDelay")}: <i className="ring on-time"/>{t("legendOnTime")} <i className="ring late"/>{t("legendLate")} <i className="ring early"/>{t("legendEarly")}</span>}
          </div>
        </section>

        <main className={`panel sheet-${sheet}`} aria-label={t("panelLabel")}>
          <button type="button" className="sheet-handle" onPointerDown={onHandleDown} onPointerUp={onHandleUp} onPointerCancel={() => { drag.current = null; }}
            onClick={onHandleKey} aria-label={t("sheetToggle")} aria-expanded={sheet !== "peek"} aria-controls="panel-scroll"><i/></button>
          <Stepper focus={focus} t={t} onStep={goStep} vehicleLabel={vehicle.data?.label} />
          <div className="panel-scroll" id="panel-scroll" ref={scroller}>
            {apiError && <p className="form-error" role="alert">{t("dataFallback")}</p>}
            <Panel language={language} t={t} focus={focus} stop={stop} line={line} lineVehicles={lineShown} allLineVehicles={lineVehicles.data}
              lineScope={scope} onScope={setLineScope} headingCount={heading.size}
              stats={{ routes: routeCount, stops: stopCount, vehicles: vehicleCount }}
              vehicle={vehicle} onPickLine={pickDeparturesLine} onPickVehicle={pickVehicle} onBack={back} onClear={clear} />
            <footer className="data-footer">
              <p>{t("attribution")}</p>
              <details className="source-details"><summary>{t("sourceDetails")}</summary><small>{sourceNotices || t("sourceInfo")}</small></details>
            </footer>
          </div>
        </main>
      </div>
    </div>
  );
}
