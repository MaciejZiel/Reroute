import { arrivalWindow, clock, delayClass, delayText, firstExplained, lineOrder, pausedNoteKey, preciseDelayText } from "./format";
import type { LineScope } from "./vehicles";
import { countText, type TranslationKey } from "./i18n";
import type { Polled } from "./usePolled";
import type { RecentStop } from "./recentStops";
import { distanceText, type NearbyStop } from "./nearby";
import type {
  Departure, FeatureCollection, Focus, Language, LineDepartures, LineDetail, NextStop, StopDepartures,
  VehicleDetail,
} from "./types";

type T = (key: TranslationKey) => string;

interface PanelProps {
  language: Language;
  t: T;
  focus: Focus;
  stop: Polled<StopDepartures>;
  line: Polled<LineDetail>;
  /** The line's vehicles after the scope was applied (what the map shows). */
  lineVehicles: FeatureCollection | null;
  allLineVehicles: FeatureCollection | null;
  lineScope: LineScope;
  onScope: (scope: LineScope) => void;
  headingCount: number;
  vehicle: Polled<VehicleDetail>;
  onPickLine: (line: LineDepartures) => void;
  onPickVehicle: (vehicleId: string) => void;
  onClear: () => void;
  recentStops: RecentStop[];
  onPickRecent: (stop: RecentStop) => void;
  nearby: NearbyStop[];
  nearbyState: "idle" | "asking" | "denied" | "unavailable" | "ready";
  onFindNearby: () => void;
}

function LineChip({ label, mode }: { label: string; mode: string }) {
  return <span className={`route-chip ${mode}`}>{label}</span>;
}

function DelayBadge({ seconds, t, big = false }: { seconds: number | null; t: T; big?: boolean }) {
  const kind = delayClass(seconds);
  // "On time" covers up to 3 min late (same as the map legend); say so, and on the big badge also say how late.
  const exact = big && seconds !== null && kind === "on-time" && Math.abs(seconds) >= 60 ? ` (${preciseDelayText(seconds, t("min"))})` : "";
  const text = seconds === null
    ? t("delayUnknown")
    : kind === "on-time" ? `${t("delayOnTime")}${exact}` : `${delayText(seconds, t("min"))} ${kind === "late" ? t("delayLate") : t("delayEarly")}`;
  return <span className={`delay-badge ${kind} ${big ? "big" : ""}`} title={kind === "on-time" ? t("onTimeHint") : undefined}>{text}</span>;
}

function inMinutes(departure: Departure, t: T) {
  return departure.minutes === 0 ? t("now") : `${departure.minutes} ${t("min")}`;
}

/** One quiet line under the next stops: why the model expects what it does at the named stop. */
function WhyLine({ stop, t, language }: { stop: NextStop; t: T; language: Language }) {
  const range = stop.lower_delay_seconds != null && stop.upper_delay_seconds != null
    ? `${t("likelyRange")}: ${preciseDelayText(stop.lower_delay_seconds, t("min"))} … ${preciseDelayText(stop.upper_delay_seconds, t("min"))}`
    : null;
  return (
    <p className="why-note">
      <span className="section-kicker">{t("whyThis")}: {stop.name}</span>
      <span className="sr-only">. </span>
      <span lang={language}>{stop.why?.summary}</span>
      {range && <small>{range}</small>}
    </p>
  );
}

/**
 * Small delay on a departure: the delay itself ("+2 min"), not "on time", so the predicted and
 * the timetable clock next to it never seem to disagree. Coloured like the map legend.
 */
function RowDelay({ seconds, t }: { seconds: number | null; t: T }) {
  if (seconds === null) return <span className="delay-badge unknown">{t("scheduledShort")}</span>;
  const kind = delayClass(seconds);
  // Whole minutes, cut like the clocks beside it: 30 s late shows the same time as the timetable.
  const minutes = Math.trunc(seconds / 60);
  const text = minutes === 0 ? t("delayOnTime") : delayText(minutes * 60, t("min"));
  return <span className={`delay-badge ${kind}`} title={t("onTimeHint")}>{text}</span>;
}

function DepartureRow({ departure, language, t }: { departure: Departure; language: Language; t: T }) {
  const expected = departure.predicted_at ?? departure.scheduled_at;
  // The window only where the model answered; elsewhere the current delay carried forward.
  const window = departure.live ? arrivalWindow(departure.scheduled_at, departure.lower_delay_seconds, departure.upper_delay_seconds, language) : null;
  return (
    <li className="departure-row">
      <span className={`departure-minutes ${departure.minutes === 0 ? "is-now" : ""}`}>{inMinutes(departure, t)}</span>
      <span className="departure-main">
        <strong>{departure.headsign || "—"}</strong>
        <small className="meta">
          {window
            ? <span className="forecast" title={t("forecastHint")}><span className="sr-only">{t("forecast")}: </span>{window}</span>
            : <span>{clock(expected, language)}</span>}
          {departure.live && departure.predicted_at && <span>{t("scheduled")} {clock(departure.scheduled_at, language)}</span>}
          {departure.platform && <span>{t("platform")} {departure.platform}</span>}
        </small>
      </span>
      {departure.live ? <RowDelay seconds={departure.delay_seconds} t={t}/> : <span className="delay-badge unknown">{t("scheduledShort")}</span>}
    </li>
  );
}

function Header({ kicker, title, subtitle, onClear, t, chip }: {
  kicker: string; title: string; subtitle?: string; onClear: () => void; t: T; chip?: React.ReactNode;
}) {
  return (
    <div className="panel-head">
      <div className="panel-title">
        <div><span className="section-kicker">{kicker}</span><h2 tabIndex={-1}>{chip}{title}</h2>{subtitle && <p className="panel-sub">{subtitle}</p>}</div>
        <button className="panel-close" onClick={onClear} aria-label={t("clear")} title={t("clear")}>×</button>
      </div>
    </div>
  );
}

function ErrorNote({ error, fallback, t, onRetry }: { error: Error | null; fallback: string; t: T; onRetry?: () => void }) {
  if (!error) return null;
  const status = (error as { status?: number }).status;
  return (
    <div className="form-error" role="alert">
      <p>{status === 503 ? t("reimport") : fallback}</p>
      {onRetry && <button type="button" className="retry-button retry-inline" onClick={onRetry}>{t("retry")}</button>}
    </div>
  );
}

/** One row per line and direction: a line serves a stop both ways, from different platforms. */
interface DirectionRow { key: string; line: LineDepartures; headsign: string; departures: Departure[] }

function directionRows(lines: LineDepartures[]): DirectionRow[] {
  const rows: DirectionRow[] = [];
  for (const line of lines) {
    const byHeadsign = new Map<string, Departure[]>();
    for (const departure of line.departures) {
      const list = byHeadsign.get(departure.headsign) ?? [];
      list.push(departure);
      byHeadsign.set(departure.headsign, list);
    }
    for (const [headsign, departures] of byHeadsign) {
      rows.push({ key: `${line.route_id}|${headsign}`, line, headsign, departures });
    }
  }
  // A fixed order (line number, then direction): sorting by the next departure moved rows
  // under the pointer at every refresh.
  return rows.sort((a, b) => lineOrder(a.line.line, b.line.line) || a.headsign.localeCompare(b.headsign, "pl"));
}

function StopView({ language, t, stop, onPickLine, onClear, focus }: PanelProps) {
  const data = stop.data;
  const lines = data ? data.lines : [];
  const rows = directionRows(lines);
  const idle = lines.filter((line) => line.departures.length === 0).sort((a, b) => lineOrder(a.line, b.line));
  const running = lines.length - idle.length;
  return (
    <>
      <Header kicker={t("stopKicker")} title={data?.stop.name ?? focus.stopName ?? ""} onClear={onClear} t={t}
        subtitle={data ? `${countText(data.platforms.length, "platform", language)} · ${countText(running, "activeLine", language)}` : undefined}/>
      <ErrorNote error={stop.error} fallback={t("stopError")} t={t} onRetry={stop.retry}/>
      {stop.loading && !data && <div className="skeleton-list" aria-hidden="true"><i/><i/><i/></div>}
      {data && data.data_mode === "demo" && <p className="panel-note">{t("demoNoTimetable")}</p>}
      {data && lines.length === 0 && <p className="panel-note">{t("stopNoLines")}</p>}
      {data && lines.length > 0 && <>
        <div className="section-line"><h3 className="section-kicker">{t("linesAtStop")}</h3>{data.live_vehicles > 0 && <span className="live-count"><i/>{data.live_vehicles} {t("liveCount")}</span>}</div>
        {rows.length > 0 && <ul className="line-list">
          {rows.map((row) => (
            <li key={row.key}>
              <button className="line-row" onClick={() => onPickLine(row.line)} aria-label={`${row.line.line} → ${row.headsign}`}>
                <span className="line-row-head"><LineChip label={row.line.line} mode={row.line.mode}/><span className="line-name">→ {row.headsign || row.line.long_name}</span><span className="chevron" aria-hidden="true">›</span></span>
                <span className="chip-row">{row.departures.slice(0, 4).map((departure) => (
                  <span key={departure.trip_id + departure.stop_id} className={`time-chip ${departure.live ? delayClass(departure.delay_seconds) : "scheduled"}`}
                    title={`${clock(departure.scheduled_at, language)} ${t("scheduled")}`}>
                    <b>{inMinutes(departure, t)}</b>
                    {departure.live && departure.delay_seconds !== null && Math.trunc(departure.delay_seconds / 60) !== 0 && <small>{delayText(Math.trunc(departure.delay_seconds / 60) * 60, t("min"))}</small>}
                  </span>))}</span>
              </button>
            </li>
          ))}
        </ul>}
        {rows.length === 0 && <p className="panel-note">{t("noDepartures")}</p>}
        {idle.length > 0 && <details className="idle-lines">
          <summary>{countText(idle.length, "idleLine", language)}</summary>
          <ul className="idle-list">{idle.map((line) => (
            <li key={line.route_id}><button className="idle-line" onClick={() => onPickLine(line)} aria-label={`${line.line} ${line.long_name}`}><LineChip label={line.line} mode={line.mode}/></button></li>
          ))}</ul>
        </details>}
        <p className="estimate-note">{t("estimateNote")}</p>
      </>}
    </>
  );
}

function LineView(props: PanelProps) {
  const { language, t, focus, stop, line, lineVehicles, allLineVehicles, lineScope, onScope, headingCount, onPickVehicle, onClear } = props;
  const detail = line.data;
  const here = stop.data?.lines.find((item) => item.route_id === focus.lineId || item.line === focus.lineLabel);
  const vehicles = (lineVehicles?.features ?? []).map((feature) => ({
    id: feature.id,
    label: String(feature.properties.label ?? feature.id),
    delay: typeof feature.properties.delay_seconds === "number" ? feature.properties.delay_seconds : null,
  })).sort((a, b) => (b.delay ?? -1e9) - (a.delay ?? -1e9));
  const measured = vehicles.filter((item) => item.delay !== null);
  const mean = measured.length ? Math.round(measured.reduce((sum, item) => sum + (item.delay ?? 0), 0) / measured.length) : null;
  return (
    <>
      <Header kicker={t("lineKicker")} title={detail?.long_name ?? ""} onClear={onClear} t={t}
        chip={<LineChip label={detail?.line ?? focus.lineLabel ?? ""} mode={detail?.mode ?? "bus"}/>}/>
      {focus.stopId && (
        <div className="scope-switch" role="group" aria-label={t("vehicleScope")}>
          <button type="button" aria-pressed={lineScope === "all"} onClick={() => onScope("all")}>{t("scopeAll")}<small>{allLineVehicles?.features.length ?? 0}</small></button>
          <button type="button" aria-pressed={lineScope === "stop"} onClick={() => onScope("stop")}>{t("scopeStop")}<small>{headingCount}</small></button>
        </div>
      )}
      <ErrorNote error={line.error} fallback={t("lineError")} t={t} onRetry={line.retry}/>
      {detail && detail.alerts.length > 0 && (
        <section className="alert-box" aria-label={t("alerts")}>
          <span className="section-kicker">{t("alerts")}</span>
          <ul>{detail.alerts.slice(0, 3).map((alert) => (
            <li key={alert.id}>{(language === "pl" ? alert.header_pl : alert.header_en) ?? alert.header_pl ?? alert.id}{alert.url && <> · <a href={alert.url} target="_blank" rel="noreferrer">{t("moreInfo")}</a></>}</li>
          ))}</ul>
        </section>
      )}
      {here && here.departures.length > 0 && <>
        <div className="section-line"><h3 className="section-kicker">{t("lineDeparturesHere")} {focus.stopName}</h3></div>
        <ul className="departure-list">{here.departures.slice(0, 4).map((departure) => <DepartureRow key={departure.trip_id} departure={departure} language={language} t={t}/>)}</ul>
      </>}
      <div className="section-line"><h3 className="section-kicker">{t("lineVehicles")} · {vehicles.length}</h3>{mean !== null && <span className="mean-delay">{t("meanDelayNow")}: <DelayBadge seconds={mean} t={t}/></span>}</div>
      {vehicles.length === 0 && lineVehicles && <p className="panel-note">{lineScope === "stop" ? `${t("noneHeadingHere")} ${focus.stopName ?? ""}.` : t("noLineVehicles")}</p>}
      <ul className="vehicle-list">{vehicles.map((vehicle) => (
        <li key={vehicle.id}><button className="vehicle-row" onClick={() => onPickVehicle(String(vehicle.id))}><span className="vehicle-id">{vehicle.label}</span><DelayBadge seconds={vehicle.delay} t={t}/><span className="chevron" aria-hidden="true">›</span></button></li>
      ))}</ul>
      <p className="estimate-note">{t("estimateNote")}</p>
    </>
  );
}

function VehicleView({ language, t, vehicle, onClear }: PanelProps) {
  const data = vehicle.data;
  const messages: Record<VehicleDetail["status"], TranslationKey | null> = {
    ok: null, layover: "statusLayover", off_route: "statusOffRoute", no_schedule: "statusNoSchedule", stale: "statusStale",
  };
  const message = data ? messages[data.status] : null;
  return (
    <>
      <Header kicker={t("vehicleKicker")} title={data ? (data.headsign ? `→ ${data.headsign}` : `${t("stepLineTitle")} ${data.line}`) : t("stepVehicleTitle")}
        subtitle={data?.label ? `#${data.label}` : undefined} onClear={onClear} t={t}
        chip={data ? <LineChip label={data.line} mode={data.mode}/> : undefined}/>
      <ErrorNote error={vehicle.error} fallback={t("lineError")} t={t} onRetry={vehicle.retry}/>
      {vehicle.loading && !data && <div className="skeleton-list" aria-hidden="true"><i/><i/></div>}
      {data && <>
        <div className="vehicle-delay"><span className="section-kicker">{t("delayVsTimetable")}</span><DelayBadge seconds={data.delay_seconds} t={t} big/>
          <small>{t("updated")} {data.age_seconds} {t("secondsAgo")}</small></div>
        {message && <p className="panel-note">{t(message)}</p>}
        {data.next_stops.length > 0 && <>
          <div className="section-line"><h3 className="section-kicker">{t("nextStops")}</h3></div>
          <ol className="stop-timeline">{data.next_stops.map((stop) => (
            <li key={stop.sequence}>
              <span className="timeline-dot"/>
              <span className="timeline-name">{stop.name}</span>
              <span className="timeline-times"><b>{clock(stop.predicted_at ?? stop.scheduled_at, language)}</b>{stop.predicted_at && <small>{t("scheduled")} {clock(stop.scheduled_at, language)}</small>}</span>
            </li>))}</ol>
        </>}
        {(() => {
          const explained = firstExplained(data.next_stops);
          const paused = pausedNoteKey(data.next_stops);
          if (explained) return <WhyLine stop={explained} t={t} language={language}/>;
          return paused && <p className="why-note why-paused" role="status">{t(paused)}</p>;
        })()}
        <p className="estimate-note">{t("estimateNote")} {data.model.kind === "baseline" ? t("modelBaseline") : `${t("modelOther")}: ${data.model.name} ${data.model.version}.`}</p>
      </>}
    </>
  );
}

export default function Panel(props: PanelProps) {
  const { focus, t } = props;
  if (focus.vehicleId) return <VehicleView {...props}/>;
  if (focus.lineId) return <LineView {...props}/>;
  if (focus.stopId) return <StopView {...props}/>;
  const { recentStops, onPickRecent, nearby, nearbyState, onFindNearby, language } = props;
  const nearbyNote: Partial<Record<PanelProps["nearbyState"], TranslationKey>> = {
    asking: "nearbyAsking", denied: "nearbyDenied", unavailable: "nearbyUnavailable",
  };
  const note = nearbyNote[nearbyState];
  return (
    <div className="idle-panel">
      <h2 tabIndex={-1}>{t("idleTitle")}</h2>
      <p className="idle-lede">{t("idleBody")}</p>
      <button type="button" className="nearby-button" onClick={onFindNearby} disabled={nearbyState === "asking"}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/><circle cx="12" cy="12" r="7.5"/></svg>
        {t("nearbyButton")}
      </button>
      {note && <p className="panel-note" role="status">{t(note)}</p>}
      {nearbyState === "ready" && nearby.length === 0 && <p className="panel-note" role="status">{t("nearbyNone")}</p>}
      {nearby.length > 0 && <>
        <div className="section-line"><h3 className="section-kicker">{t("nearbyTitle")}</h3></div>
        <ul className="recent-list">{nearby.map((item) => (
          <li key={item.id}><button type="button" className="recent-row" onClick={() => onPickRecent(item)}><span>{item.name}</span><span className="row-meta">{distanceText(item.distanceM, language)}</span></button></li>
        ))}</ul>
      </>}
      {recentStops.length > 0 && <>
        <div className="section-line"><h3 className="section-kicker">{t("recentStops")}</h3></div>
        <ul className="recent-list">{recentStops.map((item) => (
          <li key={item.id}><button type="button" className="recent-row" onClick={() => onPickRecent(item)}><span>{item.name}</span><span className="chevron" aria-hidden="true">›</span></button></li>
        ))}</ul>
      </>}
    </div>
  );
}
