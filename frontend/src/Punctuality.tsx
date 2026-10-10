import { useEffect, useMemo, useState } from "react";
import { fetchPunctuality } from "./api";
import { translate } from "./i18n";
import type { Language, LinePunctuality, PunctualityResult } from "./types";

type T = (key: Parameters<typeof translate>[1]) => string;

const WINDOWS = [30, 120, 360];
const MAX_LINES = 10;

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function signedMinutes(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function DelayTrend({ line, language, t }: { line: LinePunctuality; language: Language; t: T }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 320;
  const height = 120;
  const pad = { left: 30, right: 8, top: 10, bottom: 20 };
  const points = line.series;
  const values = points.map((point) => point.mean_delay_minutes);
  const low = Math.min(0, ...values);
  const high = Math.max(1, ...values);
  const times = points.map((point) => new Date(point.observed_at).getTime());
  const start = Math.min(...times);
  const end = Math.max(...times, start + 1);
  const x = (time: number) => pad.left + ((time - start) / (end - start)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + ((high - value) / (high - low)) * (height - pad.top - pad.bottom);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(times[index]).toFixed(1)},${y(point.mean_delay_minutes).toFixed(1)}`).join("");
  const clock = new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit" });
  const active = hover === null ? null : points[hover];

  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const position = ((event.clientX - box.left) / box.width) * width;
    let nearest = 0;
    times.forEach((time, index) => { if (Math.abs(x(time) - position) < Math.abs(x(times[nearest]) - position)) nearest = index; });
    setHover(nearest);
  }

  return (
    <figure className="delay-trend">
      <figcaption>{t("delayTrend")} · <b>{line.line}</b></figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${t("delayTrend")} ${line.line}`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[high, 0, low].filter((value, index, all) => all.indexOf(value) === index).map((value) => (
          <g key={value}>
            <line x1={pad.left} x2={width - pad.right} y1={y(value)} y2={y(value)} className={value === 0 ? "trend-zero" : "trend-grid"} />
            <text x={pad.left - 5} y={y(value) + 3} textAnchor="end">{signedMinutes(value)}</text>
          </g>
        ))}
        <text x={pad.left} y={height - 5}>{clock.format(start)}</text>
        <text x={width - pad.right} y={height - 5} textAnchor="end">{clock.format(end)}</text>
        <path d={path} className="trend-line" />
        {active && hover !== null && <g>
          <line x1={x(times[hover])} x2={x(times[hover])} y1={pad.top} y2={height - pad.bottom} className="trend-cursor" />
          <circle cx={x(times[hover])} cy={y(active.mean_delay_minutes)} r={4} className="trend-dot" />
        </g>}
      </svg>
      <p className="trend-readout">{active ? `${clock.format(new Date(active.observed_at))} · ${signedMinutes(active.mean_delay_minutes)} ${t("minutes")} · ${percent(active.on_time_share)} ${t("onTime").toLowerCase()} · ${active.vehicles} ${t("vehicles")}` : t("trendHint")}</p>
    </figure>
  );
}

export default function Punctuality({ language, t, onClose }: { language: Language; t: T; onClose: () => void }) {
  const [minutes, setMinutes] = useState(120);
  const [data, setData] = useState<PunctualityResult | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const next = await fetchPunctuality(minutes);
        if (alive) { setData(next); setError(false); }
      } catch { if (alive) setError(true); }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 60_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [minutes]);

  const lines = useMemo(() => data?.lines.slice(0, MAX_LINES) ?? [], [data]);
  const focus = lines.find((line) => line.line === selected) ?? lines[0] ?? null;
  const total = data?.lines.reduce((sum, line) => sum + line.observations, 0) ?? 0;
  const onTime = total ? (data?.lines.reduce((sum, line) => sum + line.on_time_share * line.observations, 0) ?? 0) / total : 0;

  return (
    <section className="punctuality-panel" aria-label={t("punctualityTitle")}>
      <header className="punctuality-head">
        <div><span className="section-kicker">{t("punctualityKicker")}</span><h2>{t("punctualityTitle")}</h2></div>
        <button className="panel-close" onClick={onClose} aria-label={t("close")}>×</button>
      </header>
      <div className="punctuality-toolbar">
        <div className="window-control" role="group" aria-label={t("window")}>
          {WINDOWS.map((value) => <button key={value} className={minutes === value ? "active" : ""} aria-pressed={minutes === value} onClick={() => setMinutes(value)}>{value < 60 ? `${value} min` : `${value / 60} h`}</button>)}
        </div>
        {data && <span className={`mode-tag ${data.data_mode}`}>{data.data_mode === "demo" ? t("demo") : `${data.snapshots} ${t("snapshots")}`}</span>}
      </div>
      {error && <p className="form-error">{t("punctualityError")}</p>}
      {data && lines.length === 0 && <p className="no-alternatives">{t("noPunctuality")}</p>}
      {lines.length > 0 && <>
        <div className="punctuality-summary"><strong>{percent(onTime)}</strong><span>{t("networkOnTime")}<br/><small>{t("onTimeDefinition")}</small></span></div>
        <div className="punctuality-legend" aria-hidden="true"><span><i className="seg-early"/>{t("early")}</span><span><i className="seg-on-time"/>{t("onTime")}</span><span><i className="seg-late"/>{t("late")}</span><span className="legend-note">{t("meanDelay")}</span></div>
        <ul className="punctuality-bars">
          {lines.map((line) => (
            <li key={`${line.mode}-${line.line}`}>
              <button className={`bar-row ${focus?.line === line.line ? "selected" : ""}`} onClick={() => setSelected(line.line)} onPointerEnter={() => setHovered(line.line)} onPointerLeave={() => setHovered(null)} aria-label={`${line.line}: ${percent(line.on_time_share)} ${t("onTime")}, ${percent(line.late_share)} ${t("late")}, ${signedMinutes(line.mean_delay_minutes)} min`}>
                <span className={`route-chip ${line.mode}`}>{line.line}</span>
                <span className="bar-track">
                  {line.early_share > 0 && <i className="seg-early" style={{ width: percent(line.early_share) }}/>}
                  {line.on_time_share > 0 && <i className="seg-on-time" style={{ width: percent(line.on_time_share) }}/>}
                  {line.late_share > 0 && <i className="seg-late" style={{ width: percent(line.late_share) }}/>}
                </span>
                <span className="bar-value">{signedMinutes(line.mean_delay_minutes)}<small> min</small></span>
                {hovered === line.line && <span className="bar-tooltip" role="tooltip">{percent(line.early_share)} {t("early").toLowerCase()} · {percent(line.on_time_share)} {t("onTime").toLowerCase()} · {percent(line.late_share)} {t("late").toLowerCase()}<br/>{t("median")} {signedMinutes(line.median_delay_minutes)} min · {line.observations} {t("observations")}</span>}
              </button>
            </li>
          ))}
        </ul>
        {data && data.lines.length > MAX_LINES && <small className="punctuality-more">{data.lines.length - MAX_LINES} {t("moreLines")}</small>}
        {focus && focus.series.length > 1 && <DelayTrend line={focus} language={language} t={t}/>}
      </>}
      {data && <p className="estimate-note">{data.notice}</p>}
    </section>
  );
}
