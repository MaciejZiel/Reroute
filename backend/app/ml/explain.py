"""Plain-language reasons for a prediction, from per-feature SHAP values, in English or Polish.

The model predicts how much the delay *changes* between the vehicle's latest known state and a
later stop of the same trip, so every contribution is a number of seconds added to or taken off
the current delay.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

NOTABLE_S = 5.0  # smaller effects are noise to a rider
MAX_FACTORS = 3
LANGUAGES = ("en", "pl")


@dataclass(frozen=True)
class Factor:
    feature: str
    effect_s: int  # positive: later than the current delay alone would suggest
    text: str


@dataclass(frozen=True)
class Explanation:
    summary: str
    factors: tuple[Factor, ...]


def duration(seconds: float) -> str:
    """ "45 s", "4 min", "3 min 58 s": the same abbreviations in both languages."""
    seconds = abs(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min" if rest == 0 else f"{minutes} min {rest} s"


def number(value: float, lang: str, digits: int = 0) -> str:
    """A number as the reader's locale writes it (Polish uses a decimal comma)."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "pl" else text


def _plural_pl(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class _English:
    code = "en"

    @staticmethod
    def delay(seconds: float) -> str:
        if abs(seconds) < 30:
            return "on time"
        return f"{duration(seconds)} {'late' if seconds > 0 else 'early'}"

    @staticmethod
    def stops(n: int) -> str:
        return f"{n} stop" if n == 1 else f"{n} stops"

    now = "Now {now}; expected {predicted} at this stop"
    quiet = "{lead}, no strong signal beyond the current delay."
    main = "{lead}. Main factors: {factors}."


class _Polish:
    code = "pl"

    @staticmethod
    def delay(seconds: float) -> str:
        """The delay as an object of "spodziewamy się": "opóźnienia 4 min", "przyjazdu o czasie"."""
        if abs(seconds) < 30:
            return "przyjazdu o czasie"
        if seconds > 0:
            return f"opóźnienia {duration(seconds)}"
        return f"przyjazdu {duration(seconds)} przed czasem"

    @staticmethod
    def be(seconds: float, kind: str, extra: str = "") -> str:
        """ "jest teraz opóźniony o 3 min", "były punktualne": verb and adjective agree with the
        subject (kind: now = pojazd, was = pojazd, were = pojazdy)."""
        plural = kind == "were"
        verb = {"is": "jest", "now": "jest teraz", "was": "był", "were": "były"}[kind]
        verb = f"{verb} {extra}" if extra else verb
        if abs(seconds) < 30:
            return f"{verb} {'punktualne' if plural else 'punktualny'}"
        if seconds > 0:
            return f"{verb} {'opóźnione' if plural else 'opóźniony'} o {duration(seconds)}"
        return f"{verb} przed czasem o {duration(seconds)}"

    @staticmethod
    def stops(n: int) -> str:
        return f"{n} {_plural_pl(n, 'przystanek', 'przystanki', 'przystanków')}"

    now = "Teraz pojazd {now}; na tym przystanku spodziewamy się {predicted}"
    quiet = "{lead}, brak wyraźnego sygnału poza obecnym opóźnieniem."
    main = "{lead}. Główne czynniki: {factors}."


_LANG: dict[str, Any] = {"en": _English, "pl": _Polish}

# Each phrase takes the feature value and returns a clause that continues "because ...".
_PHRASES: dict[str, dict[str, Callable[..., str]]] = {
    "en": {
        "distance": lambda L, v: (
            f"the stop is {L.stops(round(v[0]))} ahead, {duration(v[1])} of running time"
        ),
        "stops_ahead": lambda L, v: f"the stop is {L.stops(round(v))} ahead",
        "vehicle_delay_s": lambda L, v: f"the vehicle is {L.delay(v)} right now",
        "vehicle_minus_origin_s": lambda L, v: (
            f"its delay grew by {duration(v)} since the last stop"
            if v > 0
            else f"it has made up {duration(v)} since the last stop"
        ),
        "vehicle_report_age_s": lambda L, v: f"its last report is {duration(v)} old",
        "origin_delay_s": lambda L, v: f"the vehicle was {L.delay(v)} at its last stop",
        "prev_delay_change_s": lambda L, v: (
            f"its delay grew by {duration(v)} at the last stop"
            if v > 0
            else f"it made up {duration(v)} at the last stop"
        ),
        "trend_s_per_min": lambda L, v: (
            f"its delay has been {'growing' if v > 0 else 'shrinking'} by about "
            f"{number(abs(v), 'en')} s per minute"
        ),
        "seconds_since_origin": lambda L, v: f"its last recorded stop was {duration(v)} ago",
        "sched_run_s": lambda L, v: f"{duration(v)} of scheduled running time remain",
        "stop_seq": lambda L, v: f"this is stop number {round(v)} of the trip",
        "hour_frac": lambda L, v: "of the time of day",
        "weekday": lambda L, v: "of the day of the week",
        "is_tram": lambda L, v: "it is a tram" if v else "it is a bus",
        "line_recent_delay_s": lambda L, v: (
            f"other vehicles of this line were {L.delay(v)} on average in the last 15 minutes"
        ),
        "stop_recent_delay_s": lambda L, v: (
            f"vehicles at this stop were {L.delay(v)} on average in the last 30 minutes"
        ),
        "leader_delay_s": lambda L, v: f"the vehicle ahead on this line was {L.delay(v)} here",
        "headway_s": lambda L, v: f"the vehicle ahead is scheduled {duration(v)} earlier",
    },
    "pl": {
        "distance": lambda L, v: (
            f"przystanek jest {L.stops(round(v[0]))} dalej, {duration(v[1])} planowej jazdy"
        ),
        "stops_ahead": lambda L, v: f"przystanek jest {L.stops(round(v))} dalej",
        "vehicle_delay_s": lambda L, v: f"pojazd {L.be(v, 'now')}",
        "vehicle_minus_origin_s": lambda L, v: (
            f"opóźnienie wzrosło o {duration(v)} od ostatniego przystanku"
            if v > 0
            else f"pojazd nadrobił {duration(v)} od ostatniego przystanku"
        ),
        "vehicle_report_age_s": lambda L, v: f"ostatni raport pojazdu ma {duration(v)}",
        "origin_delay_s": lambda L, v: f"na ostatnim przystanku pojazd {L.be(v, 'was')}",
        "prev_delay_change_s": lambda L, v: (
            f"opóźnienie wzrosło o {duration(v)} na ostatnim przystanku"
            if v > 0
            else f"pojazd nadrobił {duration(v)} na ostatnim przystanku"
        ),
        "trend_s_per_min": lambda L, v: (
            f"opóźnienie {'rośnie' if v > 0 else 'maleje'} o ok. {number(abs(v), 'pl')} s na minutę"
        ),
        "seconds_since_origin": lambda L, v: (
            f"ostatni zarejestrowany przystanek był {duration(v)} temu"
        ),
        "sched_run_s": lambda L, v: f"zostało {duration(v)} planowej jazdy",
        "stop_seq": lambda L, v: f"to {round(v)}. przystanek kursu",
        "hour_frac": lambda L, v: "pora dnia",
        "weekday": lambda L, v: "dzień tygodnia",
        "is_tram": lambda L, v: "to tramwaj" if v else "to autobus",
        "line_recent_delay_s": lambda L, v: (
            f"inne pojazdy tej linii {L.be(v, 'were')} (średnio, ostatnie 15 min)"
        ),
        "stop_recent_delay_s": lambda L, v: (
            f"pojazdy na tym przystanku {L.be(v, 'were')} (średnio, ostatnie 30 min)"
        ),
        "leader_delay_s": lambda L, v: f"pojazd przed nim na tej linii {L.be(v, 'was', 'tu')}",
        "headway_s": lambda L, v: f"pojazd przed nim jest rozkładowo {duration(v)} wcześniej",
    },
}


def _merge_distance(items: list[tuple[str, object, float]]) -> list[tuple[str, object, float]]:
    """Stops ahead and scheduled running time measure the same thing and often pull in opposite
    directions; a rider should read one "distance" factor, not two that cancel."""
    found = {name: (value, effect) for name, value, effect in items}
    if "stops_ahead" not in found or "sched_run_s" not in found:
        return items
    stops, run = found["stops_ahead"][0], found["sched_run_s"][0]
    effect = found["stops_ahead"][1] + found["sched_run_s"][1]
    rest = [item for item in items if item[0] not in ("stops_ahead", "sched_run_s")]
    return [*rest, ("distance", (stops, run), effect)]


def _missing(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _lower(text: str, lang: str) -> str:
    return text if text.startswith("of ") else text[0].lower() + text[1:]


def _upper(text: str, lang: str) -> str:
    return text if text.startswith("of ") else text[0].upper() + text[1:]


def explain(
    names: Sequence[str],
    values: Sequence[float],
    contributions: Sequence[float],
    origin_delay_s: float,
    predicted_delay_s: float,
    lang: str = "en",
) -> Explanation:
    """Top contributing factors, largest effect first, as short sentences in ``lang``."""
    words = _LANG.get(lang, _English)
    phrases = _PHRASES[words.code]
    items: list[tuple[str, object, float]] = list(zip(names, values, contributions, strict=True))
    ranked = sorted(_merge_distance(items), key=lambda i: -abs(i[2]))
    factors = []
    for name, value, effect in ranked:
        if abs(effect) < NOTABLE_S or name not in phrases or _missing(value):
            continue
        text = phrases[name](words, value)
        factors.append(Factor(name, round(effect), _upper(text, words.code)))
        if len(factors) == MAX_FACTORS:
            break
    current = words.be(origin_delay_s, "is") if words.code == "pl" else words.delay(origin_delay_s)
    lead = words.now.format(now=current, predicted=words.delay(predicted_delay_s))
    if not factors:
        return Explanation(words.quiet.format(lead=lead), ())
    listed = "; ".join(f"{_lower(f.text, words.code)} ({f.effect_s:+d} s)" for f in factors[:2])
    return Explanation(words.main.format(lead=lead, factors=listed), tuple(factors))
