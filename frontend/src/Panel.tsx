import { clock, delayClass, delayText, lineOrder } from "./format";
import type { LineScope } from "./vehicles";
import type { TranslationKey } from "./i18n";
import type { Polled } from "./usePolled";
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
  stats: { routes: number; stops: number; vehicles: number };
  vehicle: Polled<VehicleDetail>;
  onPickLine: (line: LineDepartures) => void;
  onPickVehicle: (vehicleId: string) => void;
  onBack: () => void;
  onClear: () => void;
}

function LineChip({ label, mode }: { label: string; mode: string }) {
  return <span className={`route-chip ${mode}`}>{label}</span>;
}

function DelayBadge({ seconds, t, big = false }: { seconds: number | null; t: T; big?: boolean }) {
  const kind = delayClass(seconds);
  const text = seconds === null
    ? t("delayUnknown")
    : kind === "on-time" ? t("delayOnTime") : `${delayText(seconds, t("min"))} ${kind === "late" ? t("delayLate") : t("delayEarly")}`;
  return <span className={`delay-badge ${kind} ${big ? "big" : ""}`}>{text}</span>;
}

function inMinutes(departure: Departure, t: T) {
  return departure.minutes === 0 ? t("now") : `${departure.minutes} ${t("min")}`;
}

/** One quiet line under the next stops: what the model says moved the first prediction. */
function WhyLine({ stop, t }: { stop: NextStop; t: T }) {
  const range = stop.lower_delay_seconds != null && stop.upper_delay_seconds != null
    ? `${t("likelyRange")}: ${delayText(stop.lower_delay_seconds, t("min"))} … ${delayText(stop.upper_delay_seconds, t("min"))}`
    : null;
  return (
    <p className="why-note">
      <span className="section-kicker">{t("whyThis")}</span>
      <span lang="en">{stop.why?.summary}</span>
      {range && <small>{range}</small>}
    </p>
  );
}

function DepartureRow({ departure, language, t }: { departure: Departure; language: Language; t: T }) {
  const expected = departure.predicted_at ?? departure.scheduled_at;
  return (
    <li className="departure-row">
      <span className="departure-minutes">{inMinutes(departure, t)}</span>
      <span className="departure-main">
        <strong>{departure.headsign || "—"}</strong>
        <small>
          {clock(expected, language)}
          {departure.live && departure.predicted_at && <> · {t("scheduled")} {clock(departure.scheduled_at, language)}</>}
          {departure.platform && <> · {t("platform")} {departure.platform}</>}
        </small>
      </span>
      {departure.live ? <DelayBadge seconds={departure.delay_seconds} t={t}/> : <span className="delay-badge unknown">{t("scheduled")}</span>}
    </li>
  );
}

function Header({ kicker, title, subtitle, onBack, backLabel, onClear, t, chip }: {
  kicker: string; title: string; subtitle?: string; onBack?: () => void; backLabel?: string; onClear: () => void; t: T; chip?: React.ReactNode;
}) {
  return (
    <div className="panel-head">
      {onBack && <button className="back-button" onClick={onBack}>← {backLabel}</button>}
      <div className="panel-title">
        <div><span className="section-kicker">{kicker}</span><h2>{chip}{title}</h2>{subtitle && <p className="panel-sub">{subtitle}</p>}</div>
        <button className="panel-close" onClick={onClear} aria-label={t("clear")} title={t("clear")}>×</button>
      </div>
    </div>
  );
}

function ErrorNote({ error, fallback, t }: { error: Error | null; fallback: string; t: T }) {
  if (!error) return null;
  const status = (error as { status?: number }).status;
  return <p className="form-error" role="alert">{status === 503 ? t("reimport") : fallback}</p>;
}

function StopView({ language, t, stop, onPickLine, onClear, focus }: PanelProps) {
  const data = stop.data;
  const lines = data ? [...data.lines] : [];
  const withTimes = lines.filter((line) => line.departures.length > 0);
  const without = lines.filter((line) => line.departures.length === 0).sort((a, b) => lineOrder(a.line, b.line));
  return (
    <>
      <Header kicker={t("stopKicker")} title={data?.stop.name ?? focus.stopName ?? ""} onClear={onClear} t={t}
        subtitle={data ? `${data.platforms.length} ${t("platforms")} · ${data.lines.length} ${t("activeLines")}` : undefined}/>
      <ErrorNote error={stop.error} fallback={t("stopError")} t={t}/>
      {stop.loading && !data && <div className="skeleton-list" aria-hidden="true"><i/><i/><i/></div>}
      {data && data.data_mode === "demo" && <p className="panel-note">{t("demoNoTimetable")}</p>}
      {data && <>
        <div className="section-line"><span className="section-kicker">{t("linesAtStop")}</span>{data.live_vehicles > 0 && <span className="live-count"><i/>{data.live_vehicles} {t("liveCount")}</span>}</div>
        <ul className="line-list">
          {[...withTimes, ...without].map((line) => (
            <li key={line.route_id}>
              <button className="line-row" onClick={() => onPickLine(line)} aria-label={`${line.line} ${line.long_name}`}>
                <span className="line-row-head"><LineChip label={line.line} mode={line.mode}/><span className="line-name">{line.departures[0]?.headsign || line.long_name}</span><span className="chevron" aria-hidden="true">›</span></span>
                {line.departures.length > 0
                  ? <span className="chip-row">{line.departures.slice(0, 4).map((departure) => (
                      <span key={departure.trip_id + departure.stop_id} className={`time-chip ${departure.live ? delayClass(departure.delay_seconds) : "scheduled"}`}
                        title={`${clock(departure.scheduled_at, language)} ${t("scheduled")}`}>
                        <b>{inMinutes(departure, t)}</b>
                        {departure.live && departure.delay_seconds !== null && <small>{delayText(departure.delay_seconds, t("min"))}</small>}
                      </span>))}</span>
                  : <span className="no-departures">{t("noDepartures")}</span>}
              </button>
            </li>
          ))}
        </ul>
        <p className="estimate-note">{t("estimateNote")}</p>
      </>}
    </>
  );
}

function LineView(props: PanelProps) {
  const { language, t, focus, stop, line, lineVehicles, allLineVehicles, lineScope, onScope, headingCount, onPickVehicle, onBack, onClear } = props;
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
        onBack={focus.stopId ? onBack : undefined} backLabel={`${t("backToStop")}${focus.stopName ? `: ${focus.stopName}` : ""}`}
        chip={<LineChip label={detail?.line ?? focus.lineLabel ?? ""} mode={detail?.mode ?? "bus"}/>}/>
      {focus.stopId && (
        <div className="scope-switch" role="group" aria-label={t("vehicleScope")}>
          <button type="button" aria-pressed={lineScope === "all"} onClick={() => onScope("all")}>{t("scopeAll")}<small>{allLineVehicles?.features.length ?? 0}</small></button>
          <button type="button" aria-pressed={lineScope === "stop"} onClick={() => onScope("stop")}>{t("scopeStop")}<small>{headingCount}</small></button>
        </div>
      )}
      <ErrorNote error={line.error} fallback={t("lineError")} t={t}/>
      {detail && detail.alerts.length > 0 && (
        <section className="alert-box" aria-label={t("alerts")}>
          <span className="section-kicker">{t("alerts")}</span>
          <ul>{detail.alerts.slice(0, 3).map((alert) => (
            <li key={alert.id}>{(language === "pl" ? alert.header_pl : alert.header_en) ?? alert.header_pl ?? alert.id}{alert.url && <> · <a href={alert.url} target="_blank" rel="noreferrer">{t("moreInfo")}</a></>}</li>
          ))}</ul>
        </section>
      )}
      {here && here.departures.length > 0 && <>
        <div className="section-line"><span className="section-kicker">{t("lineDeparturesHere")} {focus.stopName}</span></div>
        <ul className="departure-list">{here.departures.slice(0, 4).map((departure) => <DepartureRow key={departure.trip_id} departure={departure} language={language} t={t}/>)}</ul>
      </>}
      <div className="section-line"><span className="section-kicker">{t("lineVehicles")} · {vehicles.length}</span>{mean !== null && <span className="mean-delay">{t("meanDelayNow")}: <DelayBadge seconds={mean} t={t}/></span>}</div>
      {vehicles.length === 0 && lineVehicles && <p className="panel-note">{lineScope === "stop" ? `${t("noneHeadingHere")} ${focus.stopName ?? ""}.` : t("noLineVehicles")}</p>}
      <ul className="vehicle-list">{vehicles.map((vehicle) => (
        <li key={vehicle.id}><button className="vehicle-row" onClick={() => onPickVehicle(String(vehicle.id))}><span className="vehicle-id">{vehicle.label}</span><DelayBadge seconds={vehicle.delay} t={t}/><span className="chevron" aria-hidden="true">›</span></button></li>
      ))}</ul>
      <p className="estimate-note">{t("estimateNote")}</p>
    </>
  );
}

function VehicleView({ language, t, focus, vehicle, onBack, onClear }: PanelProps) {
  const data = vehicle.data;
  const messages: Record<VehicleDetail["status"], TranslationKey | null> = {
    ok: null, layover: "statusLayover", off_route: "statusOffRoute", no_schedule: "statusNoSchedule", stale: "statusStale",
  };
  const message = data ? messages[data.status] : null;
  return (
    <>
      <Header kicker={t("vehicleKicker")} title={data ? `${data.line}${data.headsign ? ` → ${data.headsign}` : ""}` : focus.lineLabel ?? ""}
        subtitle={data?.label ? `#${data.label}` : undefined} onClear={onClear} t={t}
        onBack={onBack} backLabel={`${t("backToLine")} ${focus.lineLabel ?? ""}`}
        chip={data ? <LineChip label={data.line} mode={data.mode}/> : undefined}/>
      <ErrorNote error={vehicle.error} fallback={t("lineError")} t={t}/>
      {vehicle.loading && !data && <div className="skeleton-list" aria-hidden="true"><i/><i/></div>}
      {data && <>
        <div className="vehicle-delay"><span className="section-kicker">{t("delayVsTimetable")}</span><DelayBadge seconds={data.delay_seconds} t={t} big/>
          <small>{t("updated")} {data.age_seconds} {t("secondsAgo")}</small></div>
        {message && <p className="panel-note">{t(message)}</p>}
        {data.next_stops.length > 0 && <>
          <div className="section-line"><span className="section-kicker">{t("nextStops")}</span></div>
          <ol className="stop-timeline">{data.next_stops.map((stop) => (
            <li key={stop.sequence}>
              <span className="timeline-dot"/>
              <span className="timeline-name">{stop.name}</span>
              <span className="timeline-times"><b>{clock(stop.predicted_at ?? stop.scheduled_at, language)}</b>{stop.predicted_at && <small>{t("scheduled")} {clock(stop.scheduled_at, language)}</small>}</span>
            </li>))}</ol>
        </>}
        {data.next_stops[0]?.why && <WhyLine stop={data.next_stops[0]} t={t}/>}
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
  const { stats } = props;
  return (
    <div className="idle-panel">
      <span className="section-kicker">{t("idleKicker")}</span>
      <h2>{t("idleTitle")}</h2>
      <p>{t("idleBody")}</p>
      <dl className="net-stats">
        <div><dt>{t("activeLines")}</dt><dd>{stats.routes || "—"}</dd></div>
        <div><dt>{t("stops")}</dt><dd>{stats.stops || "—"}</dd></div>
        <div><dt>{t("vehicles")}</dt><dd>{stats.vehicles || "—"}</dd></div>
      </dl>
    </div>
  );
}
