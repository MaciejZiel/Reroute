import type { TranslationKey } from "./i18n";
import type { Focus } from "./types";
import type { Step } from "./vehicles";

type T = (key: TranslationKey) => string;

/**
 * Where you are, as a path: "Centrum › 131 › #8340". Earlier parts are buttons that go back to
 * that view; the last one is the current view. Shown from two parts on (stop and line).
 */
export default function Crumbs({ focus, t, onStep, vehicleLabel }: { focus: Focus; t: T; onStep: (step: Step) => void; vehicleLabel?: string | null }) {
  const items: { key: string; label: string; kind: string; go: Step }[] = [];
  if (focus.stopId && focus.stopName) items.push({ key: "stop", label: focus.stopName, kind: t("stepStopTitle"), go: 2 });
  if (focus.lineId) items.push({ key: "line", label: focus.lineLabel ?? "", kind: t("stepLineTitle"), go: 3 });
  if (focus.vehicleId) items.push({ key: "vehicle", label: `#${vehicleLabel ?? focus.vehicleId.split("/").pop()}`, kind: t("stepVehicleTitle"), go: 3 });
  // A single part would only repeat the panel's own title.
  if (items.length < 2) return null;
  return (
    <nav className="crumbs" aria-label={t("stepsLabel")}>
      <ol>
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={item.key} className={`crumb crumb-${item.key}`}>
              {last
                ? <span aria-current="page"><span className="sr-only">{item.kind}: </span>{item.label}</span>
                : <button type="button" onClick={() => onStep(item.go)}><span className="sr-only">{item.kind}: </span>{item.label}</button>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
