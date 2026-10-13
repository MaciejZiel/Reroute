import type { TranslationKey } from "./i18n";

type T = (key: TranslationKey) => string;

interface Props {
  t: T;
  showAll: boolean;
  onShowAll: (value: boolean) => void;
  allowShowAll: boolean;
  vehicleCount: number;
  showBuses: boolean;
  showTrams: boolean;
  onBuses: (value: boolean) => void;
  onTrams: (value: boolean) => void;
  punctuality: boolean;
  onPunctuality: () => void;
  zoomedOut: boolean;
}

/** Layer controls floating over the map: the city-wide vehicle switch, mode filters and the secondary view. */
export default function MapControls(props: Props) {
  const { t, showAll, onShowAll, allowShowAll, vehicleCount, showBuses, showTrams, onBuses, onTrams, punctuality, onPunctuality, zoomedOut } = props;
  return (
    <div className="map-controls">
      {allowShowAll && (
        <button type="button" role="switch" aria-checked={showAll} className={`vehicle-switch ${showAll ? "on" : ""}`} onClick={() => onShowAll(!showAll)}>
          <span className="switch-track" aria-hidden="true"><i/></span>
          <span className="switch-text"><b>{t("showAllVehicles")}</b><small>{showAll ? `${vehicleCount} ${t("inService")}` : t("showAllHint")}</small></span>
        </button>
      )}
      <div className="chip-group" role="group" aria-label={t("mapLayers")}>
        <button type="button" className="mode-chip bus" aria-pressed={showBuses} onClick={() => onBuses(!showBuses)}><i aria-hidden="true"/>{t("buses")}</button>
        <button type="button" className="mode-chip tram" aria-pressed={showTrams} onClick={() => onTrams(!showTrams)}><i aria-hidden="true"/>{t("trams")}</button>
        <button type="button" className="mode-chip plain" aria-pressed={punctuality} onClick={onPunctuality}>{t("punctualityShort")}</button>
      </div>
      {zoomedOut && <p className="zoom-hint" role="status">{t("zoomForStops")}</p>}
    </div>
  );
}
