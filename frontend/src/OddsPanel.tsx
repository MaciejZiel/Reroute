import { useEffect, useState } from "react";
import { fetchOdds } from "./api";
import { preciseDelayText } from "./format";
import type { TranslationKey } from "./i18n";
import { DAY_TYPES, MIN_ARRIVALS, cellAt, heatLevel, oneIn, serviceHours, warsawSlot } from "./odds";
import type { DayType, Language, OddsLine, StopOdds } from "./types";

type T = (key: TranslationKey) => string;

const DAY_KEYS: Record<DayType, TranslationKey> = { weekday: "oddsWeekday", saturday: "oddsSaturday", sunday: "oddsSunday" };
const DAY_SHORT: Record<DayType, TranslationKey> = { weekday: "oddsWeekdayShort", saturday: "oddsSaturdayShort", sunday: "oddsSundayShort" };
const HEAT_KEYS: TranslationKey[] = ["oddsHeat0", "oddsHeat1", "oddsHeat2", "oddsHeat3"];

const lineKey = (line: OddsLine) => `${line.line}|${line.stop_id}`;
const percent = (share: number) => `${Math.round(share * 100)}%`;

function dateText(iso: string, language: Language) {
  return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { day: "numeric", month: "short" }).format(new Date(`${iso}T12:00:00Z`));
}

interface Props {
  language: Language;
  t: T;
  onClose: () => void;
  stopId: string | null;
  stopName: string | null;
  lineLabel: string | null;
  /** A stop search of its own, so the panel works where it covers the main one (phones). */
  search: React.ReactNode;
}

/**
 * "What are the odds it is late?": for a line at the picked stop, how often it ran more than 3
 * minutes late on this kind of day at this hour, counted from the recorded arrivals of the last
 * four weeks. Plain counts, no model, so the numbers mean exactly what they say.
 */
export default function OddsPanel({ language, t, onClose, stopId, stopName, lineLabel, search }: Props) {
  const [data, setData] = useState<StopOdds | null>(null);
  const [error, setError] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [now, setNow] = useState(() => warsawSlot(new Date()));
  // The hour shown; null follows "now", a picked square holds until the stop or line changes.
  const [chosen, setChosen] = useState<{ day: DayType; hour: number } | null>(null);
  const slot = chosen ?? now;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(warsawSlot(new Date())), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    setData(null);
    setError(false);
    setPicked(null);
    setChosen(null);
    if (!stopId) return;
    const controller = new AbortController();
    fetchOdds(stopId, controller.signal).then(setData).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [stopId]);

  const lines = data?.lines ?? [];
  // The line already picked in the panel, then the one open on the left, then the first.
  const line = lines.find((item) => lineKey(item) === picked)
    ?? lines.find((item) => item.line === lineLabel)
    ?? lines[0];
  const hours = line ? serviceHours(line) : [];
  const cell = line ? cellAt(line, slot.day, slot.hour) : undefined;
  const isNow = slot.day === now.day && slot.hour === now.hour;
  const hourText = (hour: number) => `${hour}:00–${hour}:59`;

  return (
    <section className="punctuality-panel odds-panel" aria-label={t("oddsTitle")}>
      <header className="punctuality-head">
        <div><span className="section-kicker">{t("oddsKicker")}</span><h2 tabIndex={-1}>{t("oddsTitle")}</h2></div>
        <button className="panel-close" onClick={onClose} aria-label={t("close")}>×</button>
      </header>

      {!stopId && <><p className="model-lede">{t("oddsPickStop")}</p><div className="odds-search">{search}</div></>}
      {stopId && error && <p className="form-error" role="status">{t("oddsError")}</p>}
      {stopId && !data && !error && <p className="no-alternatives">{t("loading")}</p>}
      {data && !data.ready && <p className="panel-note">{t("oddsNotReady")}</p>}
      {data?.ready && lines.length === 0 && <p className="panel-note">{t("oddsNoData")}</p>}

      {data?.ready && line && <>
        <label className="odds-pick">
          <span className="section-kicker">{stopName ?? data.stop.name}</span>
          <select value={lineKey(line)} onChange={(event) => { setPicked(event.target.value); setChosen(null); }}>
            {lines.map((item) => (
              <option key={lineKey(item)} value={lineKey(item)}>
                {item.line}{item.headsign ? ` → ${item.headsign}` : ""}{item.platform ? ` (${t("platform")} ${item.platform})` : ""}
              </option>
            ))}
          </select>
        </label>

        <div className="odds-card" aria-live="polite">
          <span className="section-kicker">{isNow ? t("oddsNow") : t("oddsPicked")}: {t(DAY_KEYS[slot.day])}, {hourText(slot.hour)}</span>
          {cell && cell.n >= MIN_ARRIVALS ? <>
            {oneIn(cell.late3_share) === null
              ? <p className="odds-big"><strong>{t("oddsRarely")}</strong> <span>{t("oddsRarelyText")}</span></p>
              : <p className="odds-big"><strong>{t("oddsOneIn").replace("{n}", String(oneIn(cell.late3_share)))}</strong> <span>{t("oddsLate3")} ({percent(cell.late3_share)})</span></p>}
            <dl className="odds-facts">
              <div><dt>{t("oddsTypical")}</dt><dd>{preciseDelayText(cell.median_s, t("min"))}</dd></div>
              <div><dt>{t("oddsWorstTenth")}</dt><dd>{preciseDelayText(cell.p90_s, t("min"))} {t("oddsOrMore")}</dd></div>
              <div><dt>{t("oddsLate5")}</dt><dd>{percent(cell.late5_share)}</dd></div>
              <div><dt>{t("oddsEarly")}</dt><dd>{percent(cell.early_share)}</dd></div>
            </dl>
            <small className="odds-basis">{t("oddsBasis").replace("{n}", String(cell.n)).replace("{days}", String(cell.days))}</small>
          </> : <p className="no-alternatives">{t("oddsTooFew").replace("{n}", String(cell?.n ?? 0))}</p>}
        </div>

        <h3 className="model-heading">{t("oddsWhenHeading")}</h3>
        <p className="model-note">{t("oddsWhenText")}</p>
        <div className="odds-heat" role="group" aria-label={t("oddsWhenHeading")} style={{ gridTemplateColumns: `52px repeat(${hours.length}, minmax(0, 1fr))` }}>
          <span aria-hidden="true"/>
          {hours.map((hour) => <span key={`h${hour}`} className="odds-hour" aria-hidden="true">{hour % 3 === 0 ? hour : ""}</span>)}
          {DAY_TYPES.map((day) => <div key={day} className="odds-row">
            <span className="odds-day" aria-hidden="true">{t(DAY_SHORT[day])}</span>
            {hours.map((hour) => {
              const here = cellAt(line, day, hour);
              const level = heatLevel(here);
              const label = level === null
                ? `${t(DAY_KEYS[day])} ${hourText(hour)}: ${t("oddsTooFewShort")}`
                : `${t(DAY_KEYS[day])} ${hourText(hour)}: ${percent(here!.late3_share)} ${t("oddsLate3Short")}`;
              const selected = day === slot.day && hour === slot.hour;
              return <button key={hour} type="button" aria-pressed={selected} aria-label={label} title={label}
                className={`odds-cell ${level === null ? "heat-none" : `heat-${level}`} ${selected ? "selected" : ""} ${day === now.day && hour === now.hour ? "now" : ""}`}
                onClick={() => setChosen({ day, hour })}/>;
            })}
          </div>)}
        </div>
        <p className="odds-legend" aria-hidden="true">
          {HEAT_KEYS.map((key, level) => <span key={key}><i className={`heat-${level}`}/>{t(key)}</span>)}
          <span><i className="heat-none"/>{t("oddsTooFewShort")}</span>
        </p>
        <p className="estimate-note">
          {t("oddsNote").replace("{days}", String(data.window_days))}
          {data.first_day && data.last_day ? ` (${dateText(data.first_day, language)} – ${dateText(data.last_day, language)})` : ""}
        </p>
      </>}
    </section>
  );
}
