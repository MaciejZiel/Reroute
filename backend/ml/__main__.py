"""``python -m ml train``: build samples, split by time, fit, evaluate, export, plot."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.ml.featurize import MAX_ORIGIN_AGE_S, MAX_VEHICLE_AGE_S

MISSING_LIBRARIES = """\
The training code needs numpy, scipy and lightgbm, which are not installed ({missing} missing).

They are deliberately not project dependencies: the numpy and scipy wheels bundle LGPL/GPL
runtime libraries, so training is done on the maintainer's machine and adding them to the
project needs Company Management approval. Install them yourself, outside the project:

    pip install numpy scipy lightgbm

The server never needs them (the model is evaluated by pure Python in app/ml/ensemble.py).
"""


def require_libraries(*names: str) -> None:
    """Exit with an explanation, not a traceback, when an out-of-tree library is missing."""
    import importlib.util

    missing = [name for name in names if importlib.util.find_spec(name) is None]
    if missing:
        print(MISSING_LIBRARIES.format(missing=", ".join(missing)), file=sys.stderr)
        raise SystemExit(2)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def positive_seconds(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds, not {text}")
    return value


def positive_rounds(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1 tree, not {text}")
    return value


def run_train(args: argparse.Namespace) -> int:
    import numpy as np

    from . import dataset, evaluate, linear, report, train

    events = dataset.load_events(args.events)
    print(f"{len(events)} arrivals, {len({e.trip_id for e in events})} trips", file=sys.stderr)
    observations = dataset.Observations.load(args.observations) if args.observations else None
    moment_every = args.moment_every or dataset.MOMENT_EVERY_S
    samples = dataset.build_samples(events, observations=observations, moment_every_s=moment_every)
    folds = dataset.time_folds(events, tuple(args.fractions))
    assignment = folds.assign(samples)
    parts = {name: samples.take(assignment == i) for i, name in enumerate(dataset.FOLDS)}
    dropped = int((assignment < 0).sum())
    sample_count = len(samples)
    vehicle_delay_known_share = float(
        1 - np.isnan(samples.X[:, dataset.FEATURE_NAMES.index("vehicle_delay_s")]).mean()
    )
    del samples  # the folds hold copies; the full set is gigabytes on a week of data
    exclude = () if args.calendar_features else train.SMALL_DATA_EXCLUDED
    max_rounds = args.max_rounds or train.MAX_ROUNDS
    heads = train.fit(
        parts["train"],
        parts["early_stop"],
        parts["calibration"],
        exclude=tuple(exclude),
        max_rounds=max_rounds,
    )
    test = parts["test"]
    ridge = linear.fit(parts["train"], parts["early_stop"], heads.features)
    evaluation = evaluate.evaluate(
        heads, parts["train"], parts["calibration"], test, train.NOMINAL, ridge
    )
    first, last = min(e.actual_s for e in events), max(e.actual_s for e in events)
    # What was known when the data was pulled: a later rebuild of the folds (``make
    # ml-credibility``) cuts events and vehicle reports at this moment to get the same samples.
    known = [max(e.known_s for e in events)]
    if observations is not None and observations.newest is not None:
        known.append(observations.newest)
    known_until = max(known)
    card = report._plain(
        {
            "data": {
                "arrivals": len(events),
                "trips": len({e.trip_id for e in events}),
                "lines": len({e.line for e in events}),
                "stops": len({e.stop_id for e in events}),
                "first_arrival": iso(first),
                "last_arrival": iso(last),
                "span_hours": round((last - first) / 3600, 2),
                "last_event_epoch": last,
                "known_until_epoch": known_until,
                "service_dates": sorted({e.trip_id[:10] for e in events}),
            },
            "folds": {
                name: {
                    "samples": len(parts[name]),
                    "from": iso(folds.cuts[i]),
                    "to": iso(folds.cuts[i + 1]),
                }
                for i, name in enumerate(dataset.FOLDS)
            },
            # How the folds and the samples were made, so a later rebuild makes the same ones.
            "fold_fractions": list(args.fractions),
            "moment_every_s": moment_every,
            "samples": sample_count,
            "vehicle_delay_known_share": vehicle_delay_known_share,
            "dropped_straddling_a_boundary": dropped,
            "lags_in_stops": list(dataset.DEFAULT_LAGS),
            "features": heads.features,
            "rounds": heads.rounds,
            "linear": report.linear_summary(ridge),
            "params": train.PARAMS,
            "max_rounds": max_rounds,
            "trained_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **evaluation,
        }
    )
    serving_range = {
        "max_stops_ahead": max(dataset.DEFAULT_LAGS),
        "max_sched_run_s": float(
            np.nanmax(parts["train"].X[:, dataset.FEATURE_NAMES.index("sched_run_s")])
        ),
        "max_origin_age_s": MAX_ORIGIN_AGE_S,
        "max_vehicle_age_s": MAX_VEHICLE_AGE_S,
    }
    document = report.model_document(heads, card, serving_range)
    report.write_json(args.model_out, document, compact=True)
    report.write_json(args.report_dir / "report.json", card)
    ranked = report.write_plots(args.report_dir, heads, evaluation, test)
    table = report.results_table(card)
    (args.report_dir / "results.md").write_text(table, encoding="utf-8")
    print(table)
    gain = card["gain_vs_best_baseline"]
    print(
        f"best baseline (chosen on the calibration fold): {card['best_baseline']}\n"
        f"model MAE gain over it: {gain['value']:.1%} "
        f"(95% CI {gain['low']:.1%} to {gain['high']:.1%})\n"
        f"promoted as default predictor: {card['promoted']}\n"
        f"version {document['version']}; top features: {[n for n, _ in ranked['importance'][:5]]}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m ml", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("train", help="train, evaluate and export a model")
    fit.add_argument("--events", type=Path, required=True, help="CSV from ml/pull.sh")
    fit.add_argument(
        "--observations", type=Path, help="CSV from ml/pull.sh: the vehicles' reported delays"
    )
    fit.add_argument("--model-out", type=Path, default=Path("models/delay_model.json"))
    fit.add_argument("--report-dir", type=Path, default=Path("../docs/ml"))
    fit.add_argument(
        "--fractions",
        type=float,
        nargs=3,
        default=(0.68, 0.76, 0.84),
        metavar=("TRAIN_END", "EARLY_STOP_END", "CALIBRATION_END"),
        help="fold boundaries as fractions of the recorded time span",
    )
    fit.add_argument(
        "--calendar-features",
        action="store_true",
        help="also use hour, weekday and stop number (only sensible with several days of data)",
    )
    fit.add_argument(
        "--moment-every",
        type=positive_seconds,
        metavar="SECONDS",
        help="simulate one prediction per this many seconds of a trip's life (default 60); "
        "a larger value gives fewer, less correlated samples and needs less memory",
    )
    fit.add_argument(
        "--max-rounds",
        type=positive_rounds,
        metavar="TREES",
        help="at most this many trees per head (default 800); the server walks every tree for "
        "each prediction and its reasons, so this bounds its latency",
    )
    frozen = commands.add_parser(
        "evaluate-frozen",
        help="score an unchanged model file on arrivals after its training window",
    )
    frozen.add_argument("--model", type=Path, default=Path("models/delay_model.json"))
    frozen.add_argument("--events", type=Path, required=True)
    frozen.add_argument("--observations", type=Path)
    frozen.add_argument(
        "--since", help="UTC time (ISO 8601); default: the last arrival the model was trained on"
    )
    frozen.add_argument("--out", type=Path, help="also write the table to this markdown file")
    cred = commands.add_parser(
        "credibility",
        help="add the ridge comparison and the interval calibration to the stored model's card",
    )
    cred.add_argument("--model", type=Path, default=Path("models/delay_model.json"))
    cred.add_argument("--events", type=Path, required=True)
    cred.add_argument("--observations", type=Path)
    cred.add_argument("--report-dir", type=Path, default=Path("../docs/ml"))
    cred.add_argument(
        "--known-until",
        help="UTC time (ISO 8601) the training data was pulled; later arrivals are ignored",
    )
    cred.add_argument("--allow-mismatch", action="store_true")
    args = parser.parse_args()
    if args.command == "credibility":
        require_libraries("numpy")
        from . import credibility
        from .frozen import parse_utc

        card, problems = credibility.add_credibility(
            args.model,
            args.events,
            args.observations,
            args.report_dir,
            parse_utc(args.known_until) if args.known_until else None,
            args.allow_mismatch,
        )
        print("\n".join(problems) or "folds identical to the training run")
        print((args.report_dir / "results.md").read_text(encoding="utf-8"))
        return 0
    if args.command == "evaluate-frozen":
        require_libraries("numpy")
        from . import frozen as frozen_check

        since = frozen_check.parse_utc(args.since) if args.since else None
        text = frozen_check.render(
            frozen_check.evaluate_frozen(args.model, args.events, args.observations, since)
        )
        print(text)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        return 0
    require_libraries("numpy", "scipy", "lightgbm")
    import numpy as np

    np.seterr(all="raise", under="ignore")
    return run_train(args)


if __name__ == "__main__":
    raise SystemExit(main())
