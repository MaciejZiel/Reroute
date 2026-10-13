import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapCanvas, { type MapRequest } from "./MapCanvas";
import Panel from "./Panel";
import Punctuality from "./Punctuality";
import SearchBox from "./SearchBox";
import { fetchDepartures, fetchLine, fetchLineVehicles, fetchNetwork, fetchVehicle, fetchVehicles } from "./api";
import { geometryBounds } from "./format";
import { translate, type TranslationKey } from "./i18n";
import { usePolled } from "./usePolled";
import type { FeatureCollection, Focus, Language, LineDepartures, StopMatch } from "./types";
import "./styles.css";

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (name === "pin") return <svg {...common}><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>;
  if (name === "clock") return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
  if (name === "layers") return <svg {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>;
  if (name === "bus") return <svg {...common}><rect x="5" y="3" width="14" height="17" rx="3"/><path d="M5 8h14M8 20v2m8-2v2M8 16h.01M16 16h.01"/><path d="M8 5h8"/></svg>;
  return <svg {...common}><path d="M3 12h18M6 6l-3 6 3 6m12-12 3 6-3 6M8 3l4 18 4-18" /></svg>;
}

const NO_FOCUS: Focus = { stopId: null, stopName: null, lineId: null, lineLabel: null, vehicleId: null };

export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("reroute-language") === "en" ? "en" : "pl");
  const [focus, setFocus] = useState<Focus>(NO_FOCUS);
  const [showBuses, setShowBuses] = useState(true);
  const [showTrams, setShowTrams] = useState(true);
  const [showPunctuality, setShowPunctuality] = useState(false);
  const [fit, setFit] = useState<MapRequest<[[number, number], [number, number]]> | null>(null);
  const [flyTo, setFlyTo] = useState<MapRequest<[number, number]> | null>(null);
  const [demoVehicles, setDemoVehicles] = useState<FeatureCollection | null>(null);
  const [now, setNow] = useState(() => new Date());
  const panelRef = useRef<HTMLElement>(null);
  const nonce = useRef(0);
  const t = useCallback((key: TranslationKey) => translate(language, key), [language]);

  useEffect(() => { localStorage.setItem("reroute-language", language); }, [language]);
  useEffect(() => {
    const clock = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(clock);
  }, []);

  const network = usePolled("network", fetchNetwork, 10 * 60_000);
  const allVehicles = usePolled("vehicles", fetchVehicles, 15_000);
  const stop = usePolled(focus.stopId, (signal) => fetchDepartures(focus.stopId as string, signal), 20_000);
  const line = usePolled(focus.lineId, (signal) => fetchLine(focus.lineId as string, signal), 10 * 60_000);
  const lineVehicles = usePolled(focus.lineId, (signal) => fetchLineVehicles(focus.lineId as string, signal), 15_000);
  const vehicle = usePolled(focus.vehicleId, (signal) => fetchVehicle(focus.vehicleId as string, signal), 15_000);

  // Zoom to the line's route when it arrives.
  useEffect(() => {
    const bounds = geometryBounds(line.data?.shape ?? null);
    if (bounds) setFit({ value: bounds, nonce: ++nonce.current });
  }, [line.data?.route_id]);

  // On a narrow screen the panel sits below the map: bring it into view when something is picked.
  useEffect(() => {
    if (window.innerWidth <= 820 && (focus.stopId || focus.lineId || focus.vehicleId)) {
      panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [focus.stopId, focus.lineId, focus.vehicleId]);

  // With a line picked only that line's vehicles are shown; otherwise the whole fleet.
  const liveVehicles = focus.lineId ? lineVehicles.data : allVehicles.data;

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
    setFocus({ ...NO_FOCUS, stopId: id, stopName: name });
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

  const apiError = network.error !== null && !networkData;
  const hasLiveVehicles = allVehicles.data?.data_mode === "live";
  const hasLiveSchedule = networkData?.data_mode === "live";
  const isLive = hasLiveVehicles || hasLiveSchedule;
  const feedLabel = hasLiveVehicles ? t("live") : hasLiveSchedule ? t("scheduleOnly") : t("demo");
  const sourceNotices = [...new Set([networkData?.notice, allVehicles.data?.notice].filter(Boolean))].join(" ");
  const picked = stop.data?.platforms ?? [];
  const hasFocus = focus.stopId || focus.lineId || focus.vehicleId;

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Reroute home"><span className="brand-mark"><Icon name="route" size={21} /></span><span>reroute<span className="brand-period">.</span></span></a>
        <div className="topbar-center"><div className="product-heading"><strong>{t("productTitle")}</strong><span>{t("productSubtitle")}</span></div><span className="city-pill"><span className="city-dot"/>Warszawa</span></div>
        <div className="topbar-actions"><div className={`feed-status ${isLive ? "is-live" : "is-demo"}`}><span className="pulse-dot" />{feedLabel}</div><button className="language-toggle" onClick={() => setLanguage(language === "pl" ? "en" : "pl")} aria-label={language === "pl" ? "Zmień język" : "Change language"}>{language === "pl" ? "EN" : "PL"}<span>⌄</span></button></div>
      </header>

      <section className="workspace">
        <aside className="left-rail">
          <div className="rail-heading"><div><span className="section-kicker">{t("overview")}</span><h1>{t("networkTitle")}</h1></div><span className="live-clock"><Icon name="clock" size={14}/>{new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Warsaw" }).format(now)}</span></div>
          <div className="network-stats"><div className="stat-card"><span className="stat-icon mint"><Icon name="route"/></span><strong>{routeCount.toString().padStart(2, "0")}</strong><span>{t("activeLines")}</span></div><div className="stat-card"><span className="stat-icon coral"><Icon name="pin"/></span><strong>{stopCount.toString().padStart(2, "0")}</strong><span>{t("stops")}</span></div><div className="stat-card"><span className="stat-icon gold"><Icon name="bus"/></span><strong>{vehicleCount.toString().padStart(2, "0")}</strong><span>{t("vehicles")}</span></div></div>
          <div className="map-heading"><div><span className="section-kicker">{t("mapLayers")}</span><p>{t("toggleLayers")}</p></div><Icon name="layers" size={17}/></div>
          <div className="layer-controls"><button className={`layer-toggle ${showBuses ? "selected" : ""}`} aria-pressed={showBuses} onClick={() => setShowBuses(!showBuses)}><span className="layer-swatch bus-swatch"/><span>{t("buses")}</span><span className="toggle-track"><i/></span></button><button className={`layer-toggle ${showTrams ? "selected" : ""}`} aria-pressed={showTrams} onClick={() => setShowTrams(!showTrams)}><span className="layer-swatch tram-swatch"/><span>{t("trams")}</span><span className="toggle-track"><i/></span></button></div>
          <button className={`punctuality-toggle ${showPunctuality ? "selected" : ""}`} aria-pressed={showPunctuality} onClick={() => setShowPunctuality(!showPunctuality)}><span className="stat-icon gold"><Icon name="clock" size={16}/></span><span><strong>{t("punctualityTitle")}</strong><small>{t("punctualityCaption")}</small></span></button>
          <div className="how-it-works"><span className="section-kicker">{t("howItWorks")}</span><ol><li><b>1</b><span>{t("stepStop")}</span></li><li><b>2</b><span>{t("stepLine")}</span></li><li><b>3</b><span>{t("stepVehicle")}</span></li></ol></div>
          {apiError && <div className="left-note has-error" role="status"><span className="note-line"/><p>{t("dataFallback")}</p></div>}
          <div className="left-footer"><span className="footer-mark">R</span><span>URBAN MOBILITY<br/><b>INTELLIGENCE</b></span><span className="version">v0.2</span></div>
        </aside>

        <section className="map-stage" aria-label={t("mapTitle")}>
          <MapCanvas network={networkData} vehicles={visibleVehicles} language={language} showBuses={showBuses} showTrams={showTrams}
            focusLine={focus.lineId ? line.data : null} picked={picked} selectedVehicleId={focus.vehicleId} fit={fit} flyTo={flyTo}
            onStop={pickStop} onLine={pickLine} onVehicle={pickVehicle} />
          <SearchBox t={t} onPick={pickSearch} />
          {showPunctuality && <Punctuality language={language} t={t} onClose={() => setShowPunctuality(false)}/>}
          {!networkData && <div className="map-loading"><span className="loading-ring"/>{t("loading")}</div>}
          <div className="map-legend"><span><i className="legend-dot bus-swatch"/>{t("buses")}</span><span><i className="legend-dot tram-swatch"/>{t("trams")}</span><span><i className="legend-stop"/>{t("stops")}</span><span className="legend-delay">{t("legendDelay")}: <i className="ring on-time"/>{t("legendOnTime")} <i className="ring late"/>{t("legendLate")} <i className="ring early"/>{t("legendEarly")} <i className="ring unknown"/>{t("legendUnknown")}</span></div>
          {!hasFocus && <div className="map-selection-hint"><span className="map-hint-number">1</span><div><strong>{t("mapPromptTitle")}</strong><span>{t("mapPrompt")}</span></div></div>}
        </section>

        <aside className="right-panel" ref={panelRef} aria-live="polite">
          <Panel language={language} t={t} focus={focus} stop={stop} line={line} lineVehicles={lineVehicles.data} vehicle={vehicle}
            onPickLine={pickDeparturesLine} onPickVehicle={pickVehicle} onBack={back} onClear={clear} />
          <div className="data-footer"><div className="data-footer-head"><span className="section-kicker">{t("sources")}</span><span className="source-count">03 {t("sourcesCount")}</span></div><p>{t("attribution")}</p><details className="source-details"><summary>{t("sourceDetails")}</summary><small>{sourceNotices || t("sourceInfo")}</small></details><div className="data-source-chips"><span>WARSAW API</span><span>GTFS</span><span>OPENSTREETMAP</span></div></div>
        </aside>
      </section>
    </main>
  );
}
