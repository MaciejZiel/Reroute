"""``GET /api/model``: the served model's card, static, straight from the model file.

Nothing is computed per request and the database is not touched. When the baseline is serving
(no model file, an unpromoted model, an unreadable file) the answer says so.
"""

from __future__ import annotations

from fastapi import APIRouter

from .predict import get_predictor
from .schemas import CardCoverage, CardInterval, CardScore, ModelCardResponse

router = APIRouter(prefix="/api")

LIMITS = [
    "Trained on three and a half weekdays and tested on one weekday afternoon; weekends, "
    "holidays and bad weather were never seen.",
    "Delays are interpolated between vehicle reports, and the model is scored against these "
    "derived times, not against a ground-truth feed.",
    "The 80% interval is calibrated overall; it is checked per distance and per hour here, only "
    "on the hours the test afternoon covers, and it is too narrow in the rush hours.",
    "Where its inputs are stale or too far ahead (more than 12 stops) the baseline answers: the "
    "current delay persists.",
]

SKIP_CHECKS = ("scheduled (zero delay)", "historical median (line, stop, hour)")


def _interval(value: dict) -> CardInterval:
    return CardInterval(value=value["value"], low=value["low"], high=value["high"])


def _score(name: str, score: dict) -> CardScore:
    return CardScore(
        name=name,
        mae_s=_interval(score["mae"]),
        rmse_s=_interval(score["rmse"]),
        bias_s=score["bias"]["value"],
        mae_by_horizon_s={
            label: row["mae"]["value"] for label, row in score.get("by_horizon", {}).items()
        },
    )


def _coverage(block: dict | None, part: str, window: str, held_out: bool) -> list[CardCoverage]:
    rows = (block or {}).get(part, {})
    return [
        CardCoverage(
            label=label,
            predictions=row["n"],
            coverage=_interval(row["coverage"]),
            held_out=held_out,
            window=window,
        )
        for label, row in rows.items()
        if row.get("enough_data", True)
    ]


def card_response(model) -> ModelCardResponse:
    card = model.card
    data = card.get("data", {})
    folds = card.get("folds", {})
    interval = card.get("interval", {})
    calibration = card.get("calibration")
    hours = _coverage(calibration, "by_hour", "held out", True)
    for key, name in (
        ("calibration_window", "calibration window"),
        ("early_stop_window", "early-stopping window"),
    ):
        hours += _coverage(card.get(key), "by_hour", name, False)
    hours.sort(key=lambda row: (row.label, not row.held_out))
    gain = card.get("gain_vs_best_baseline")
    ridge_gain = card.get("gain_vs_ridge")
    return ModelCardResponse(
        status="model",
        version=model.version,
        kind="LightGBM quantile regression (median and 10%/90% quantiles of the delay change), "
        "evaluated by a pure-Python tree walker",
        promoted=model.promoted,
        trained_at=card.get("trained_at"),
        training_window=(
            {"from": folds["train"]["from"], "to": folds["calibration"]["to"]}
            if "train" in folds and "calibration" in folds
            else None
        ),
        test_window=(
            {"from": folds["test"]["from"], "to": folds["test"]["to"]} if "test" in folds else None
        ),
        data={
            key: data[key]
            for key in (
                "arrivals",
                "trips",
                "lines",
                "stops",
                "first_arrival",
                "last_arrival",
                "span_hours",
                "service_dates",
            )
            if key in data
        },
        sample_counts={name: fold["samples"] for name, fold in folds.items()},
        best_baseline=card.get("best_baseline"),
        gain_vs_best_baseline=_interval(gain) if gain else None,
        gain_vs_ridge=_interval(ridge_gain) if ridge_gain else None,
        scores=[
            _score(name, score)
            for name, score in card.get("scores", {}).items()
            if name not in SKIP_CHECKS
        ],
        interval_nominal=interval.get("nominal"),
        interval_mean_width_s=interval.get("model_mean_width_s"),
        calibration_by_horizon=_coverage(calibration, "by_horizon", "held out", True),
        calibration_by_hour=hours,
        serving_range=model.serving_range or None,
        features=list(model.feature_names),
        limits=LIMITS,
    )


@router.get("/model", response_model=ModelCardResponse, tags=["model"])
def model_card() -> ModelCardResponse:
    """Model version, training window, test metrics and interval calibration (static)."""
    predictor = get_predictor()
    model = getattr(predictor, "model", None)
    if model is None:
        return ModelCardResponse(status="baseline", limits=LIMITS[-1:])
    return card_response(model)
