import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapCanvas, { type MapRequest } from "./MapCanvas";
import MapLayers from "./MapLayers";
import ModelPanel from "./ModelPanel";
import OddsPanel from "./OddsPanel";
import Panel from "./Panel";
import Punctuality from "./Punctuality";
import SearchBox from "./SearchBox";
import Crumbs from "./Crumbs";
import { nearestStations, type NearbyStop } from "./nearby";
import { fetchDepartures, fetchLine, fetchLineVehicles, fetchNetwork, fetchVehicle, fetchVehicles } from "./api";
import { geometryBounds } from "./format";
import { translate, type TranslationKey } from "./i18n";
import { usePolled } from "./usePolled";
import { loadRecentStops, saveRecentStops, withRecentStop, type RecentStop } from "./recentStops";
import { applyPageTheme, hasStoredTheme, initialTheme, saveTheme, type Theme } from "./theme";
import { headingVehicleIds, scopeVehicles, type LineScope, type Step } from "./vehicles";
import type { FeatureCollection, Focus, Language, LineDepartures, StopMatch, StopSummary } from "./types";
import "./styles.css";

function ThemeIcon({ theme }: { theme: Theme }) {
  // Shows the theme a click switches to: a sun in the dark theme, a moon in the light one.
  return theme === "dark"
    ? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/></svg>
    : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></svg>;
}

function RouteIcon() {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8 18h6.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H16"/></svg>;
}

const NO_FOCUS: Focus = { stopId: null, stopName: null, stopCoordinate: null, lineId: null, lineLabel: null, vehicleId: null };
type Sheet = "peek" | "half" | "full";
const SHEETS: Sheet[] = ["peek", "half", "full"];

export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("reroute-language") === "en" ? "en" : "pl");
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [focus, setFocus] = useState<Focus>(NO_FOCUS);
  const [recentStops, setRecentStops] = useState<RecentStop[]>(loadRecentStops);
  const [here, setHere] = useState<{ state: "idle" | "asking" | "denied" | "unavailable" | "ready"; at?: [number, number] }>({ state: "idle" });
  const [showAll, setShowAll] = useState(false);
  const [lineScope, setLineScope] = useState<LineScope>("all");
  const [showBuses, setShowBuses] = useState(true);
  const [showTrams, setShowTrams] = useState(true);
  const [showPunctuality, setShowPunctuality] = useState(false);
  const [showModel, setShowModel] = useState(false);
  const [showOdds, setShowOdds] = useState(false);
  const punctualityButton = useRef<HTMLButtonElement>(null);
  const modelButton = useRef<HTMLButtonElement>(null);
  const oddsButton = useRef<HTMLButtonElement>(null);
  // A side panel takes the focus when it opens and gives it back to its button when it closes
  // (the close button, Escape); otherwise a keyboard user lands on the page's start.
  const closeSide = useCallback(() => {
    const opener = showPunctuality ? punctualityButton.current : showModel ? modelButton.current : showOdds ? oddsButton.current : null;
    setShowPunctuality(false);
    setShowModel(false);
    setShowOdds(false);
    opener?.focus();
  }, [showPunctuality, showModel, showOdds]);
  useEffect(() => {
    if (!showPunctuality && !showModel && !showOdds) return;
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>(".punctuality-panel h2")?.focus());
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.defaultPrevented) closeSide(); };
    document.addEventListener("keydown", escape);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("keydown", escape); };
  }, [showPunctuality, showModel, showOdds, closeSide]);
  const [zoomedOut, setZoomedOut] = useState(true);
  const [sheet, setSheet] = useState<Sheet>("peek");
  const [fit, setFit] = useState<MapRequest<[[number, number], [number, number]]> | null>(null);
  const [flyTo, setFlyTo] = useState<MapRequest<[number, number]> | null>(null);
  const [demoVehicles, setDemoVehicles] = useState<FeatureCollection | null>(null);
  const nonce = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const t = useCallback((key: TranslationKey) => translate(language, key), [language]);

  useEffect(() => { localStorage.setItem("reroute-language", language); document.documentElement.lang = language; document.title = translate(language, "documentTitle"); }, [language]);

  useEffect(() => { applyPageTheme(theme); }, [theme]);
  // Follow the system setting until the visitor picks a theme themselves.
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!query) return;
    const follow = () => { if (!hasStoredTheme()) setTheme(query.matches ? "light" : "dark"); };
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);
  const toggleTheme = () => { const next: Theme = theme === "dark" ? "light" : "dark"; saveTheme(next); setTheme(next); };

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
  // A new pick moves keyboard and screen-reader focus to the panel's heading, so the next Tab
  // reaches the panel's own list instead of starting again from the top bar. Not on first load.
  // (Compared with the last seen pick, not a first-run flag: StrictMode runs effects twice on mount.)
  const lastPick = useRef<string | null>(null);
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
    const key = `${focus.stopId}|${focus.lineId}|${focus.vehicleId}`;
    const first = lastPick.current === null;
    if (key === lastPick.current) return;
    lastPick.current = key;
    if (first) return;
    const frame = requestAnimationFrame(() => scroller.current?.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [focus.stopId, focus.lineId, focus.vehicleId]);
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
  const nearby = useMemo<NearbyStop[]>(() => (here.at ? nearestStations(networkData ?? null, here.at) : []), [networkData, here.at]);
  const findNearby = useCallback(() => {
    if (!navigator.geolocation) { setHere({ state: "unavailable" }); return; }
    setHere({ state: "asking" });
    navigator.geolocation.getCurrentPosition(
      (position) => setHere({ state: "ready", at: [position.coords.longitude, position.coords.latitude] }),
      (error) => setHere({ state: error.code === error.PERMISSION_DENIED ? "denied" : "unavailable" }),
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  }, []);
  const vehicleCount = allVehicles.data?.features.length ?? 0;

  const pickStop = useCallback((id: string, name: string, coordinate: [number, number]) => {
    setFocus({ ...NO_FOCUS, stopId: id, stopName: name, stopCoordinate: coordinate });
    setRecentStops((list) => { const next = withRecentStop(list, { id, name, coordinate }); saveRecentStops(next); return next; });
    setLineScope("all");
    setFlyTo({ value: coordinate, nonce: ++nonce.current });
  }, []);
  const pickSearch = useCallback((match: StopMatch) => pickStop(match.id, match.name, [match.longitude, match.latitude]), [pickStop]);
  const pickLine = useCallback((routeId: string, label: string) => setFocus((current) => ({ ...current, lineId: routeId, lineLabel: label, vehicleId: null })), []);
  const pickDeparturesLine = useCallback((item: LineDepartures) => pickLine(item.route_id, item.line), [pickLine]);
  const pickVehicle = useCallback((vehicleId: string) => {
    setFocus((current) => {
      const feature = (lineVehicles.data?.features.find((item) => item.id === vehicleId))
        ?? allVehicles.data?.features.find((item) => item.id === vehicleId);
      const label = feature ? String(feature.properties.line) : current.lineLabel;
      // A vehicle of another line (clicked with every vehicle shown) starts that line's path; the
      // picked stop stays only if that line calls there.
      const sameLine = label === current.lineLabel;
      // Departures not loaded yet: nothing says the line misses the stop, so keep it.
      const servesStop = stop.data ? stop.data.lines.some((item) => item.line === label) : true;
      const stopKept = current.stopId && (sameLine || servesStop) ? current : { ...current, stopId: null, stopName: null, stopCoordinate: null };
      const lineId = sameLine ? current.lineId ?? label : stop.data?.lines.find((item) => item.line === label)?.route_id ?? label;
      return { ...stopKept, lineId, lineLabel: label, vehicleId };
    });
  }, [allVehicles.data, lineVehicles.data, stop.data]);
  // Picked from the panel's list, the vehicle may be off screen: bring it into view. A vehicle
  // clicked on the map is already in view, so the map stays where the visitor put it.
  const pickVehicleFromList = useCallback((vehicleId: string) => {
    const feature = lineVehicles.data?.features.find((item) => item.id === vehicleId);
    if (feature?.geometry.type === "Point") setFlyTo({ value: feature.geometry.coordinates as [number, number], nonce: ++nonce.current, keepZoom: true });
    pickVehicle(vehicleId);
  }, [lineVehicles.data, pickVehicle]);
  const pickRecent = useCallback((item: RecentStop) => pickStop(item.id, item.name, item.coordinate), [pickStop]);
  const clear = useCallback(() => setFocus(NO_FOCUS), []);
  // A crumb returns to the view where that step is the one to choose.
  const goStep = useCallback((step: Step) => setFocus((current) => {
    if (step === 1) return NO_FOCUS;
    if (step === 2) return { ...current, lineId: null, lineLabel: null, vehicleId: null };
    return { ...current, vehicleId: null };
  }), []);

  const apiError = network.error !== null && !networkData;
  const hasLiveVehicles = allVehicles.data?.data_mode === "live";
  const hasLiveSchedule = networkData?.data_mode === "live";
  const isLive = hasLiveVehicles || hasLiveSchedule;
  // Until the first answer arrives nothing is known about the feed: say so instead of "demo".
  const connecting = !allVehicles.data && !networkData && !apiError && !allVehicles.error;
  const feedLabel = connecting ? t("connecting") : hasLiveVehicles ? t("live") : hasLiveSchedule ? t("scheduleOnly") : t("demo");
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

  const feedStatus = <span className={`feed-status ${connecting ? "is-connecting" : isLive ? "is-live" : "is-demo"}`}><span className="pulse-dot" />{feedLabel}</span>;

  return (
    <div className="app">
      <main className={`panel sheet-${sheet}`} aria-label={t("panelLabel")}>
        <button type="button" className="sheet-handle" onPointerDown={onHandleDown} onPointerUp={onHandleUp} onPointerCancel={() => { drag.current = null; }}
          onClick={onHandleKey} aria-label={t("sheetToggle")} aria-expanded={sheet !== "peek"} aria-controls="panel-scroll"><i/></button>
        <header className="panel-top">
          <div className="brand-row">
            <a className="brand" href="./" aria-label="Reroute"><span className="brand-mark"><RouteIcon /></span><span className="brand-name">reroute</span></a>
            {feedStatus}
            <div className="top-actions">
              <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={t(theme === "dark" ? "themeToLight" : "themeToDark")} title={t(theme === "dark" ? "themeToLight" : "themeToDark")}><ThemeIcon theme={theme}/></button>
              <button type="button" className="language-toggle" onClick={() => setLanguage(language === "pl" ? "en" : "pl")} aria-label={language === "pl" ? "Switch to English" : "Przełącz na polski"}>{language === "pl" ? "EN" : "PL"}</button>
            </div>
          </div>
          <h1 className="sr-only">{t("productTitle")}</h1>
          {/* On a phone the search sits in the bottom sheet: open it fully, so the results have room above the keyboard. */}
          <SearchBox t={t} onPick={pickSearch} onFocus={() => { if (window.innerWidth <= 820) setSheet("full"); }} />
          <Crumbs focus={focus} t={t} onStep={goStep} vehicleLabel={vehicle.data?.label} />
        </header>
        <div className="panel-scroll" id="panel-scroll" ref={scroller}>
          {apiError && <p className="form-error" role="alert">{t("dataFallback")}</p>}
          <Panel language={language} t={t} focus={focus} stop={stop} line={line} lineVehicles={lineShown} allLineVehicles={lineVehicles.data}
            lineScope={scope} onScope={setLineScope} headingCount={heading.size}
            vehicle={vehicle} onPickLine={pickDeparturesLine} onPickVehicle={pickVehicleFromList} onClear={clear}
            recentStops={recentStops} onPickRecent={pickRecent}
            nearby={nearby} nearbyState={here.state === "ready" && !networkData ? "asking" : here.state} onFindNearby={findNearby} />
          <footer className="data-footer">
            <div className="more-row">
              <button ref={punctualityButton} type="button" className="text-button" aria-pressed={showPunctuality} onClick={() => { setShowPunctuality(!showPunctuality); setShowModel(false); setShowOdds(false); }}>{t("punctualityShort")}</button>
              <button ref={modelButton} type="button" className="text-button" aria-pressed={showModel} onClick={() => { setShowModel(!showModel); setShowPunctuality(false); setShowOdds(false); }}>{t("modelChip")}</button>
              <button ref={oddsButton} type="button" className="text-button" aria-pressed={showOdds} onClick={() => { setShowOdds(!showOdds); setShowPunctuality(false); setShowModel(false); }}>{t("oddsButton")}</button>
            </div>
            <p>{t("attribution")}</p>
            <details className="source-details"><summary>{t("sourceDetails")}</summary><small>{sourceNotices || t("sourceInfo")}</small></details>
          </footer>
        </div>
      </main>

      <section className="map-stage" aria-label={t("mapTitle")}>
        <MapCanvas network={networkData} vehicles={visibleVehicles} vehicleLook={focus.lineId ? "focus" : "dense"}
          showBuses={showBuses} showTrams={showTrams} focusLine={focus.lineId ? line.data : null} picked={picked}
          selectedVehicleId={focus.vehicleId} fit={fit} flyTo={flyTo} label={t("mapTitle")}
          onStop={pickStop} onLine={pickLine} onVehicle={pickVehicle} onZoomedOut={setZoomedOut} theme={theme} />
        <MapLayers t={t} showBuses={showBuses} showTrams={showTrams} onBuses={setShowBuses} onTrams={setShowTrams}
          showAll={showAll} onShowAll={setShowAll} allowShowAll={!focus.lineId} vehicleCount={vehicleCount} />
        {zoomedOut && !hasFocus && networkData && !showPunctuality && !showModel && !showOdds && <p className="zoom-hint" role="status">{t("zoomForStops")}</p>}
        {showPunctuality && <Punctuality language={language} t={t} onClose={closeSide}/>}
        {showModel && <ModelPanel language={language} t={t} onClose={closeSide}/>}
        {showOdds && <OddsPanel language={language} t={t} onClose={closeSide} stopId={focus.stopId} stopName={focus.stopName} lineLabel={focus.lineLabel}
          search={<SearchBox t={t} onPick={pickSearch}/>}/>}
        {!networkData && !apiError && <div className="map-loading" role="status"><span className="loading-ring"/>{t("loading")}</div>}
        {apiError && (
          <div className="map-error" role="alert">
            <strong>{t("networkErrorTitle")}</strong>
            <p>{t("networkErrorBody")}</p>
            <button type="button" className="retry-button" onClick={() => setNetworkAttempt((n) => n + 1)}>{t("retry")}</button>
          </div>
        )}
        {visibleVehicles && (
          <div className="map-legend" aria-label={t("legend")} title={t("onTimeHint")}>
            <span className="legend-title">{t("legendDelay")}</span>
            <span><i className="ring on-time"/>{t("legendOnTime")}</span><span><i className="ring late"/>{t("legendLate")}</span><span><i className="ring early"/>{t("legendEarly")}</span>
          </div>
        )}
      </section>
    </div>
  );
}
