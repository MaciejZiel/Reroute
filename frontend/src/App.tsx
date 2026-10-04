import { useCallback, useEffect, useMemo, useState } from "react";
import MapCanvas from "./MapCanvas";
import { fetchNetwork, fetchVehicles, postSimulation } from "./api";
import { translate } from "./i18n";
import type { FeatureCollection, Language, Selection, SimulationResult } from "./types";
import "./styles.css";

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (name === "arrow") return <svg {...common}><path d="M7 17 17 7M7 7h10v10" /></svg>;
  if (name === "pin") return <svg {...common}><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>;
  if (name === "clock") return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
  if (name === "layers") return <svg {...common}><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>;
  if (name === "bus") return <svg {...common}><rect x="5" y="3" width="14" height="17" rx="3"/><path d="M5 8h14M8 20v2m8-2v2M8 16h.01M16 16h.01"/><path d="M8 5h8"/></svg>;
  return <svg {...common}><path d="M3 12h18M6 6l-3 6 3 6m12-12 3 6-3 6M8 3l4 18 4-18" /></svg>;
}

export default function App() {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("reroute-language") === "en" ? "en" : "pl");
  const [network, setNetwork] = useState<FeatureCollection | null>(null);
  const [vehicles, setVehicles] = useState<FeatureCollection | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [showBuses, setShowBuses] = useState(true);
  const [showTrams, setShowTrams] = useState(true);
  const [disruption, setDisruption] = useState<"closure" | "slowdown">("closure");
  const [duration, setDuration] = useState(30);
  const [simulation, setSimulation] = useState<SimulationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState(false);
  const [simulationError, setSimulationError] = useState("");
  const [now, setNow] = useState(() => new Date());
  const t = useCallback((key: Parameters<typeof translate>[1]) => translate(language, key), [language]);

  useEffect(() => { localStorage.setItem("reroute-language", language); }, [language]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [nextNetwork, nextVehicles] = await Promise.all([fetchNetwork(), fetchVehicles()]);
        if (!alive) return;
        setNetwork(nextNetwork);
        setVehicles(nextVehicles);
        setApiError(false);
      } catch { if (alive) setApiError(true); }
    };
    void load();
    const interval = window.setInterval(() => { void load(); }, 15_000);
    const clock = window.setInterval(() => setNow(new Date()), 1_000);
    return () => { alive = false; window.clearInterval(interval); window.clearInterval(clock); };
  }, []);

  // Give demo vehicles a small, clearly labeled movement so the map feels alive without a live feed.
  useEffect(() => {
    if (!vehicles || vehicles.data_mode !== "demo") return;
    const base = vehicles;
    const started = Date.now();
    const timer = window.setInterval(() => {
      const phase = (Date.now() - started) / 8_000;
      setVehicles({ ...base, features: base.features.map((feature, index) => {
        if (feature.geometry.type !== "Point") return feature;
        const [lng, lat] = feature.geometry.coordinates;
        const offset = phase + index * 1.7;
        return { ...feature, geometry: { ...feature.geometry, coordinates: [lng + Math.sin(offset) * 0.0015, lat + Math.cos(offset) * 0.0012] } };
      }) });
    }, 120);
    return () => window.clearInterval(timer);
  }, [vehicles?.data_mode]);

  const routeCount = useMemo(() => network?.features.filter((feature) => feature.properties.entity_type === "route").length ?? 0, [network]);
  const stopCount = useMemo(() => network?.features.filter((feature) => feature.properties.entity_type === "stop").length ?? 0, [network]);
  const vehicleCount = useMemo(() => vehicles?.features.length ?? 0, [vehicles]);

  async function runSimulation() {
    if (!selection) return;
    setBusy(true); setSimulationError(""); setSimulation(null);
    try {
      setSimulation(await postSimulation({ target_type: selection.type, target_id: selection.id, disruption_type: disruption, duration_minutes: duration }));
    } catch (error) { setSimulationError(error instanceof Error ? error.message : t("noRoutes")); }
    finally { setBusy(false); }
  }

  const hasLiveVehicles = vehicles?.data_mode === "live";
  const hasLiveSchedule = network?.data_mode === "live";
  const isLive = hasLiveVehicles || hasLiveSchedule;
  const feedLabel = hasLiveVehicles ? t("live") : hasLiveSchedule ? t("scheduleOnly") : t("demo");
  const sourceNotices = [...new Set([network?.notice, vehicles?.notice].filter(Boolean))].join(" ");
  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Reroute home"><span className="brand-mark"><Icon name="route" size={21} /></span><span>reroute<span className="brand-period">.</span></span></a>
        <div className="topbar-center"><span className="eyebrow">{t("eyebrow")}</span><span className="city-pill"><span className="city-dot"/>Warszawa</span></div>
        <div className="topbar-actions"><div className={`feed-status ${isLive ? "is-live" : "is-demo"}`}><span className="pulse-dot" />{feedLabel}</div><button className="language-toggle" onClick={() => setLanguage(language === "pl" ? "en" : "pl")} aria-label="Change language">{language === "pl" ? "EN" : "PL"}<span>⌄</span></button></div>
      </header>

      <section className="workspace">
        <aside className="left-rail">
          <div className="rail-heading"><div><span className="section-kicker">{t("overview")}</span><h1>{t("networkTitle")}</h1></div><span className="live-clock"><Icon name="clock" size={14}/>{new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit" }).format(now)}</span></div>
          <div className="network-stats"><div className="stat-card"><span className="stat-icon mint"><Icon name="route"/></span><strong>{routeCount.toString().padStart(2, "0")}</strong><span>{t("activeLines")}</span></div><div className="stat-card"><span className="stat-icon coral"><Icon name="pin"/></span><strong>{stopCount.toString().padStart(2, "0")}</strong><span>{t("stops")}</span></div><div className="stat-card"><span className="stat-icon gold"><Icon name="bus"/></span><strong>{vehicleCount.toString().padStart(2, "0")}</strong><span>{t("vehicles")}</span></div></div>
          <div className="map-heading"><div><span className="section-kicker">{t("mapLayers")}</span><p>{t("toggleLayers")}</p></div><Icon name="layers" size={17}/></div>
          <div className="layer-controls"><button className={`layer-toggle ${showBuses ? "selected" : ""}`} aria-pressed={showBuses} onClick={() => setShowBuses(!showBuses)}><span className="layer-swatch bus-swatch"/><span>{t("buses")}</span><span className="toggle-track"><i/></span></button><button className={`layer-toggle ${showTrams ? "selected" : ""}`} aria-pressed={showTrams} onClick={() => setShowTrams(!showTrams)}><span className="layer-swatch tram-swatch"/><span>{t("trams")}</span><span className="toggle-track"><i/></span></button></div>
          <div className="left-note"><span className="note-line"/><p>{apiError ? t("dataFallback") : t("selectHint")}</p><span className="note-arrow"><Icon name="arrow" size={15}/></span></div>
          <div className="left-footer"><span className="footer-mark">R</span><span>URBAN MOBILITY<br/><b>INTELLIGENCE</b></span><span className="version">v0.1</span></div>
        </aside>

        <section className="map-stage" aria-label={t("mapTitle")}>
          <MapCanvas network={network} vehicles={vehicles} language={language} showBuses={showBuses} showTrams={showTrams} selection={selection} onSelect={(next) => { setSelection(next); setSimulation(null); }} />
          <div className="map-top-label"><span className="map-live-indicator"/><span>{t("mapView")}</span><span className="map-coordinate">52°13′41″N&nbsp; 21°00′47″E</span></div>
          {!network && <div className="map-loading"><span className="loading-ring"/>{t("loading")}</div>}
          <div className="map-legend"><span><i className="legend-dot bus-swatch"/>{t("buses")}</span><span><i className="legend-dot tram-swatch"/>{t("trams")}</span><span><i className="legend-stop"/>{t("stops")}</span></div>
          <div className="map-bottom-left"><span className="map-scale"><i/>1 km</span><span className="map-area">ŚRÓDMIEŚCIE <b>·</b> WARSAW</span></div>
          <div className="map-selection-hint"><Icon name="pin" size={15}/>{t("selectHint")}</div>
        </section>

        <aside className="right-panel">
          <div className="panel-title"><div><span className="section-kicker">{t("desk")}</span><h2>{t("scenarioTitle")}</h2></div><span className="step-number">01<span> / 02</span></span></div>
          <div className="panel-rule"/>
          <label className="field-label" htmlFor="target">{t("target")}</label>
          <div className={`target-card ${selection ? "has-selection" : ""}`} id="target"><span className="target-icon"><Icon name={selection?.type === "stop" ? "pin" : "route"} size={18}/></span><span className="target-copy">{selection ? <><small>{selection.type === "stop" ? t("selectedStop") : t("selectedRoute")}</small><strong>{selection.label}</strong></> : <span className="target-placeholder">{t("chooseTarget")}</span>}</span><span className="target-state">{selection ? <i/> : <Icon name="arrow" size={16}/>}</span></div>

          <div className="field-block"><span className="field-label">{t("disruption")}</span><div className="segmented-control"><button className={disruption === "closure" ? "active" : ""} onClick={() => setDisruption("closure")}><Icon name="pin" size={15}/>{t("closure")}</button><button className={disruption === "slowdown" ? "active" : ""} onClick={() => setDisruption("slowdown")}><Icon name="route" size={15}/>{t("slowdown")}</button></div></div>
          <div className="duration-head"><span className="field-label">{t("duration")}</span><span className="duration-value">{duration}<small> {t("minutes")}</small></span></div>
          <div className="duration-control"><input aria-label={t("duration")} type="range" min="5" max="120" step="5" value={duration} onChange={(event) => setDuration(Number(event.target.value))} style={{ "--range-progress": `${((duration - 5) / 115) * 100}%` } as React.CSSProperties}/><div className="range-labels"><span>5 {t("minutes")}</span><span>120 {t("minutes")}</span></div></div>
          <button className="run-button" disabled={!selection || busy} onClick={() => void runSimulation()}><span>{busy ? t("simulating") : t("simulate")}</span><span className="run-button-icon"><Icon name="arrow" size={17}/></span></button>
          {simulationError && <p className="form-error" role="alert">{simulationError}</p>}
          {!selection && <p className="form-hint"><span>↖</span>{t("chooseTarget")}</p>}

          <div className="impact-section"><div className="impact-heading"><span className="section-kicker">{t("impact")}</span><span className={`impact-status ${simulation ? "calculated" : ""}`}><i/>{simulation ? t("calculated") : t("awaiting")}</span></div>
            {simulation ? <div className="results"><div className="impact-score"><span>{t("impactScore")}</span><strong>{simulation.impact_score}<small> pts</small></strong><p>{t("scoreInfo")}</p></div><div className="result-metrics"><div><span>{t("affectedRoutes")}</span><strong>{simulation.affected_routes.length.toString().padStart(2, "0")}</strong></div><div><span>{t("affectedStops")}</span><strong>{simulation.affected_stops.length.toString().padStart(2, "0")}</strong></div></div><div className="affected-lines">{simulation.affected_routes.map((route) => <span key={route.id} className={route.mode}>{route.label}</span>)}</div><p className="estimate-note">{simulation.notice}</p></div> : <div className="impact-empty"><span className="impact-orbit"><i/><b/></span><p>{t("impactEmpty")}</p></div>}
          </div>
          <div className="data-footer"><div className="data-footer-head"><span className="section-kicker">{t("sources")}</span><span className="source-count">03 {t("sourcesCount")}</span></div><p>{t("attribution")}</p><details className="source-details"><summary>{t("sourceDetails")}</summary><small>{sourceNotices || t("sourceInfo")}</small></details><div className="data-source-chips"><span>WARSAW API</span><span>GTFS</span><span>OPENSTREETMAP</span></div></div>
        </aside>
      </section>
    </main>
  );
}
