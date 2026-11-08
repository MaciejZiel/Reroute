import { useEffect, useId, useRef, useState } from "react";
import type { TranslationKey } from "./i18n";

type T = (key: TranslationKey) => string;

interface Props {
  t: T;
  showBuses: boolean;
  showTrams: boolean;
  onBuses: (value: boolean) => void;
  onTrams: (value: boolean) => void;
  showAll: boolean;
  onShowAll: (value: boolean) => void;
  allowShowAll: boolean;
  vehicleCount: number;
}

function LayersIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/></svg>;
}

/**
 * Everything that changes what the map draws, behind one button, so the map itself stays clear
 * (progressive disclosure). The menu closes on Escape, on a click outside and returns focus.
 */
export default function MapLayers(props: Props) {
  const { t, showBuses, showTrams, onBuses, onTrams, showAll, onShowAll, allowShowAll, vehicleCount } = props;
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); toggle.current?.focus(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  const row = (label: string, checked: boolean, onChange: (value: boolean) => void, swatch?: string, hint?: string) => (
    <label className="layer-row">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {swatch && <i className={`layer-swatch ${swatch}`} aria-hidden="true"/>}
      <span>{label}{hint && <small>{hint}</small>}</span>
    </label>
  );

  return (
    <div className="map-layers" ref={box}>
      <button ref={toggle} type="button" className="layers-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <LayersIcon/><span>{t("mapLayers")}</span>
      </button>
      {open && (
        <div className="layers-menu" id={id} role="group" aria-label={t("mapLayers")}>
          {row(t("trams"), showTrams, onTrams, "tram")}
          {row(t("buses"), showBuses, onBuses, "bus")}
          {allowShowAll && row(t("showAllVehicles"), showAll, onShowAll, undefined, showAll ? `${vehicleCount} ${t("inService")}` : t("showAllHint"))}
        </div>
      )}
    </div>
  );
}
