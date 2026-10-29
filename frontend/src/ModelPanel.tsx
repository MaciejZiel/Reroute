import { useEffect, useState } from "react";
import { fetchModel } from "./api";
import type { TranslationKey } from "./i18n";
import { comparedScores, coverageVerdict, horizonLabel, hoursByWindow, percentText, secondsText, warsawClock, warsawDate } from "./modelCard";
import type { CardCoverage, Language, ModelCard } from "./types";

type T = (key: TranslationKey) => string;

const LIMITS: TranslationKey[] = ["modelLimit1", "modelLimit2", "modelLimit3", "modelLimit4"];

/** Position on the 60% to 100% scale of the coverage bars, as a percentage of their width. */
const at = (share: number) => Math.min(100, Math.max(0, ((share - 0.6) / 0.4) * 100));

function CoverageRow({ row, nominal, language, label, t }: { row: CardCoverage; nominal: number; language: Language; label: string; t: T }) {
  const verdict = coverageVerdict(row.coverage.value, nominal);
  return (
    <li className={`coverage-row ${verdict}`}>
      <span className="coverage-label">{label}<small>{row.predictions.toLocaleString(language === "pl" ? "pl-PL" : "en-GB")} {t("modelPredictions")}</small></span>
      <span className="coverage-track" aria-hidden="true">
        <i className="coverage-range" style={{ left: `${at(row.coverage.low)}%`, width: `${at(row.coverage.high) - at(row.coverage.low)}%` }} />
        <i className="coverage-nominal" style={{ left: `${at(nominal)}%` }} />
        <i className="coverage-dot" style={{ left: `${at(row.coverage.value)}%` }} />
      </span>
      <span className="coverage-value">{percentText(language, row.coverage.value)}</span>
    </li>
  );
}

export default function ModelPanel({ language, t, onClose }: { language: Language; t: T; onClose: () => void }) {
  const [card, setCard] = useState<ModelCard | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchModel(controller.signal).then(setCard).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);

  const scores = card ? comparedScores(card.scores, card.best_baseline) : [];
  const peak = Math.max(1, ...scores.map(({ score }) => score.mae_s.value));
  const nominal = card?.interval_nominal ?? 0.8;
  const hours = card ? hoursByWindow(card.calibration_by_hour) : { heldOut: [], tuned: [] };
  const trained = card?.training_window;
  const sameDay = Boolean(trained && warsawDate(trained.from, language) === warsawDate(trained.to, language));
  const gain = card?.gain_vs_best_baseline;
  const trees = scores.find(({ key }) => key === "predTrees")?.score;

  return (
    <section className="punctuality-panel model-panel" aria-label={t("modelTitle")}>
      <header className="punctuality-head">
        <div><span className="section-kicker">{t("modelKicker")}</span><h2>{t("modelTitle")}</h2></div>
        <button className="panel-close" onClick={onClose} aria-label={t("close")}>×</button>
      </header>
      {error && <p className="form-error" role="status">{t("modelLoadError")}</p>}
      {!card && !error && <p className="no-alternatives">{t("loading")}</p>}
      {card?.status === "baseline" && <p className="no-alternatives">{t("modelBaselineOnly")}</p>}
      {card?.status === "model" && <>
        <p className="model-lede">{t("modelOneEvening")}</p>
        <dl className="model-facts">
          <div><dt>{t("modelVersion")}</dt><dd>{card.version}</dd></div>
          {trained && <div><dt>{t("modelTrainedOn")}</dt><dd>
            {sameDay ? warsawDate(trained.from, language) : `${warsawDate(trained.from, language)} – ${warsawDate(trained.to, language)}`}, {warsawClock(trained.from, language)}–{warsawClock(trained.to, language)}
          </dd></div>}
          {card.data?.arrivals ? <div><dt>{t("modelRecorded")}</dt><dd>
            {card.data.arrivals.toLocaleString(language === "pl" ? "pl-PL" : "en-GB")} {t("modelArrivals")}, {card.data.trips?.toLocaleString(language === "pl" ? "pl-PL" : "en-GB")} {t("modelTrips")}
          </dd></div> : null}
          {card.test_window && <div><dt>{t("modelTestWindow")}</dt><dd>{warsawDate(card.test_window.from, language)}, {warsawClock(card.test_window.from, language)}–{warsawClock(card.test_window.to, language)}</dd></div>}
        </dl>

        {scores.length > 0 && <>
          <h3 className="model-heading">{t("modelTestHeading")}</h3>
          <p className="model-note">{t("modelMaeNote")}</p>
          <ul className="mae-bars">
            {scores.map(({ key, score }) => (
              <li key={key} className={key === "predTrees" ? "ours" : ""}>
                <span className="mae-name">{t(key)}</span>
                <span className="mae-value">{secondsText(language, score.mae_s.value)}<small> ({secondsText(language, score.mae_s.low).replace(" s", "")}–{secondsText(language, score.mae_s.high)})</small></span>
                <span className="mae-track" aria-hidden="true"><i style={{ width: `${(score.mae_s.value / peak) * 100}%` }} /></span>
              </li>
            ))}
          </ul>
          {gain && trees && <p className="model-note">{t("modelGain")} <b>{percentText(language, gain.value)}</b> ({percentText(language, gain.low)}–{percentText(language, gain.high)}).</p>}
          {card.gain_vs_ridge && <p className="model-note">{t("modelGainRidge")} <b>{percentText(language, card.gain_vs_ridge.value)}</b>.</p>}
        </>}

        {card.calibration_by_horizon.length > 0 && <>
          <h3 className="model-heading">{t("modelRangeHeading")}</h3>
          <p className="model-note">{t("modelRangeText")}</p>
          <p className="model-axis" aria-hidden="true">{t("modelByDistance")} · {t("modelAxis")}</p>
          <ul className="coverage-list">
            {card.calibration_by_horizon.map((row) => <CoverageRow key={row.label} row={row} nominal={nominal} language={language} label={horizonLabel(language, row.label)} t={t} />)}
          </ul>
          {hours.heldOut.length > 0 && <>
            <p className="model-axis" aria-hidden="true">{t("modelByHour")}</p>
            <ul className="coverage-list">
              {hours.heldOut.map((row) => <CoverageRow key={`h${row.label}`} row={row} nominal={nominal} language={language} label={row.label} t={t} />)}
            </ul>
            <p className="model-note">{t("modelHoursNote")}</p>
          </>}
        </>}

        <h3 className="model-heading">{t("modelKnowsHeading")}</h3>
        <ul className="model-limits">{LIMITS.map((key) => <li key={key}>{t(key)}</li>)}</ul>
        <p className="model-note"><a href="https://github.com/MaciejZiel/Reroute#delay-model" target="_blank" rel="noreferrer">{t("modelFullCard")}</a></p>
      </>}
    </section>
  );
}
