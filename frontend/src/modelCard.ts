import type { CardCoverage, CardScore, Language } from "./types";

/** The three predictors the panel compares, in the order they are drawn. Other names are ignored. */
export const COMPARED = [
  { match: "persistence", key: "predPersistence" },
  { match: "ridge", key: "predLinear" },
  { match: "gradient boosted", key: "predTrees" },
] as const;

export type ComparedKey = (typeof COMPARED)[number]["key"];

export function comparedScores(scores: CardScore[]): { key: ComparedKey; score: CardScore }[] {
  return COMPARED.flatMap(({ match, key }) => {
    const score = scores.find((item) => item.name.includes(match));
    return score ? [{ key, score }] : [];
  });
}

const HORIZONS: Record<string, string> = {
  "under 2 min": "poniżej 2 min",
  "2 to 5 min": "2 do 5 min",
  "5 to 10 min": "5 do 10 min",
  "10 min or more": "10 min lub więcej",
};

export function horizonLabel(language: Language, label: string): string {
  return language === "pl" ? HORIZONS[label] ?? label : label;
}

/** "79.9%" or "79,9%": a share with one decimal, in the reader's number format. */
export function percentText(language: Language, share: number, digits = 1): string {
  return `${new Intl.NumberFormat(language === "pl" ? "pl-PL" : "en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(share * 100)}%`;
}

export function secondsText(language: Language, seconds: number): string {
  return `${new Intl.NumberFormat(language === "pl" ? "pl-PL" : "en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(seconds)} s`;
}

/** How far a held-out coverage is from the promise: "close" within 3 points, else "off". */
export function coverageVerdict(coverage: number, nominal: number): "close" | "off" {
  return Math.round(Math.abs(coverage - nominal) * 1000) <= 30 ? "close" : "off";
}

/** Held-out rows first for each hour; windows the model was tuned on follow, so they cannot pass for evidence. */
export function hoursByWindow(rows: CardCoverage[]): { heldOut: CardCoverage[]; tuned: CardCoverage[] } {
  return { heldOut: rows.filter((row) => row.held_out), tuned: rows.filter((row) => !row.held_out) };
}

/** Warsaw wall-clock "21:14" of an ISO instant, the zone the evening is told in. */
export function warsawClock(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Warsaw" }).format(new Date(iso));
}

export function warsawDate(iso: string, language: Language): string {
  return new Intl.DateTimeFormat(language === "pl" ? "pl-PL" : "en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Warsaw" }).format(new Date(iso));
}
