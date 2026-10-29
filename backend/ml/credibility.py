"""Add the comparison model and the interval calibration to an already trained model file.

Nothing is retrained: the model is read exactly as the server reads it (the pure-Python
evaluator), the samples and the four time folds are rebuilt the way ``python -m ml train`` built
them, the ridge model is fitted on the training fold, and everything goes through the same
``evaluate_predictions`` that a fresh training run uses. The result is written into the model
file's card (the trees, and so the version, are untouched) and into ``docs/ml``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.ml.featurize import FEATURE_NAMES
from app.ml.model import DelayModel

from . import calibration, dataset, evaluate, linear, report

FOLD_KEYS = ("train", "early_stop", "calibration", "test")
REFRESHED = (
    "scores",
    "interval",
    "calibration",
    "calibration_window",
    "early_stop_window",
    "gain_vs_ridge",
)


def model_quantiles(model: DelayModel):
    """(median, lower, upper) per sample from the model file, like ``Heads.predict`` (the same
    arithmetic as ``DelayModel.predict_one``, without its rounding to whole seconds)."""
    columns = [FEATURE_NAMES.index(name) for name in model.feature_names]

    def predict(samples: dataset.Samples):
        base = samples.base_delay
        out = np.empty((3, len(samples)))
        for i, row in enumerate(samples.X[:, columns].tolist()):
            median = base[i] + model.heads["median"].predict(row)
            out[0, i] = median
            out[1, i] = min(base[i] + model.heads["lower"].predict(row), median) - model.shift_s
            out[2, i] = max(base[i] + model.heads["upper"].predict(row), median) + model.shift_s
        return out[0], out[1], out[2]

    return predict


def rebuild(
    model: DelayModel,
    events_path: Path,
    observations_path: Path | None,
    known_until: float | None,
) -> tuple[dict[str, dataset.Samples], list[str]]:
    """The four folds of the original training run, and the checks that they are the same."""
    card = model.card
    if known_until is None:  # models trained from now on record what was known at the pull
        known_until = card["data"].get("known_until_epoch")
    events = [
        e
        for e in dataset.load_events(events_path)
        if e.actual_s <= card["data"]["last_event_epoch"]
        and (known_until is None or e.known_s <= known_until)
    ]
    observations = (
        dataset.Observations.load(observations_path, known_until) if observations_path else None
    )
    # Cards from before these keys were recorded used the defaults.
    samples = dataset.build_samples(
        events,
        observations=observations,
        moment_every_s=card.get("moment_every_s", dataset.MOMENT_EVERY_S),
    )
    fractions = card.get("fold_fractions")
    folds = (
        dataset.time_folds(events, tuple(fractions)) if fractions else dataset.time_folds(events)
    )
    assignment = folds.assign(samples)
    parts = {name: samples.take(assignment == i) for i, name in enumerate(FOLD_KEYS)}
    problems = []
    stated = card["data"].get("arrivals")
    if stated is not None and len(events) != stated:
        problems.append(f"{len(events)} arrivals, the card says {stated}")
    for name in FOLD_KEYS:
        stated = card.get("folds", {}).get(name, {}).get("samples")
        if stated is not None and len(parts[name]) != stated:
            problems.append(f"fold {name}: {len(parts[name])} samples, the card says {stated}")
    return parts, problems


def add_credibility(
    model_path: Path,
    events_path: Path,
    observations_path: Path | None,
    report_dir: Path,
    known_until: float | None = None,
    allow_mismatch: bool = False,
) -> tuple[dict, list[str]]:
    """Score the stored model, write the card, the charts and the tables; return card and issues."""
    model = DelayModel.load(model_path)
    parts, problems = rebuild(model, events_path, observations_path, known_until)
    if problems and not allow_mismatch:
        raise SystemExit(
            "the rebuilt data does not match what the model was trained on:\n  "
            + "\n  ".join(problems)
            + "\nPass --known-until (the UTC time the training data was pulled; the cards of newer "
            "models record it) or --allow-mismatch, which labels the refreshed blocks as coming "
            "from rebuilt folds."
        )
    ridge = linear.fit(parts["train"], parts["early_stop"], model.feature_names)
    predict = model_quantiles(model)
    evaluation = evaluate.evaluate_predictions(
        predict,
        model.shift_s,
        parts["train"],
        parts["calibration"],
        parts["test"],
        float(model.card["interval"]["nominal"]),
        ridge,
    )
    early = parts["early_stop"]
    evaluation["early_stop_window"] = calibration.interval_report(
        early,
        *predict(early)[1:],
        evaluation["calibration"]["nominal"],
        evaluate.horizon_masks(early),
    )
    stored = json.loads(model_path.read_text(encoding="utf-8"))
    card = stored["card"]
    plain = report._plain(evaluation)
    previous_mae = card.get("scores", {}).get(evaluate.GBT, {}).get("mae", {}).get("value")
    # The serving decision (promoted, best baseline, gain) stays as the training run recorded it:
    # a rebuild must never flip it. Only the new evidence is added or refreshed.
    for key in REFRESHED:
        if key in plain:
            card[key] = plain[key]
    card["linear"] = report.linear_summary(ridge)
    card["credibility_added"] = {
        "note": "calibration and the ridge comparison were computed from the stored model, "
        "without retraining, "
        + (
            "on folds identical to the training run's"
            if not problems
            else "on folds rebuilt from the recorded data, which differ from the training run's "
            "(see rebuild_differences); the tree scores are recomputed on the rebuilt folds"
        ),
        "tree_mae_before": previous_mae,
        "tree_mae_after": plain["scores"][evaluate.GBT]["mae"]["value"],
        "rebuild_differences": problems,
    }
    stored["card"] = card
    report.write_json(model_path, stored, compact=True)
    report.write_comparison_plots(report_dir, evaluation)
    report.write_json(report_dir / "report.json", card)
    (report_dir / "results.md").write_text(report.results_table(card), encoding="utf-8")
    return card, problems
