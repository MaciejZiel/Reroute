import type { TranslationKey } from "./i18n";
import type { Focus } from "./types";
import { currentStep, type Step } from "./vehicles";

type T = (key: TranslationKey) => string;

/** The three steps of the flow as a progress rail; finished steps can be clicked to go back to them. */
export default function Stepper({ focus, t, onStep, vehicleLabel }: { focus: Focus; t: T; onStep: (step: Step) => void; vehicleLabel?: string | null }) {
  const current = currentStep(focus);
  const steps: { step: Step; title: string; value: string | null }[] = [
    { step: 1, title: t("stepStopTitle"), value: focus.stopName },
    { step: 2, title: t("stepLineTitle"), value: focus.lineLabel },
    { step: 3, title: t("stepVehicleTitle"), value: focus.vehicleId ? `#${vehicleLabel ?? focus.vehicleId.split("/").pop()}` : null },
  ];
  return (
    <ol className="stepper" aria-label={t("stepsLabel")}>
      {steps.map(({ step, title, value }) => {
        const done = value !== null;
        const active = step === current;
        const content = <>
          <span className="step-num" aria-hidden="true">{done ? "✓" : step}</span>
          <span className="step-text"><small>{title}</small><b>{value ?? "—"}</b></span>
        </>;
        return (
          <li key={step} className={`step ${done ? "done" : ""} ${active ? "active" : ""}`} aria-current={active ? "step" : undefined}>
            {done && !(step === current && step === 3) ? <button type="button" onClick={() => onStep(step)}>{content}</button> : <span className="step-body">{content}</span>}
          </li>
        );
      })}
    </ol>
  );
}
