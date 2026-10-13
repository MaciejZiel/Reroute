"""``python -m ml train``: build samples, split by time, fit, evaluate, export, plot."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import dataset, evaluate, report, train


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def results_table(card: dict) -> str:
    """The comparison as a markdown table (also what the README quotes)."""
    scores, interval = card["scores"], card["interval"]
    horizons = list(next(iter(scores.values()))["by_horizon"])
    lines = [
        "| predictor | MAE s (95% CI) | RMSE s (95% CI) | bias s | "
        + " | ".join(f"MAE {h}" for h in horizons)
        + " |",
        "| --- | --- | --- | --- | " + " | ".join("---" for _ in horizons) + " |",
    ]
    for name, score in scores.items():
        cells = [f"{score['by_horizon'][h]['mae']['value']:.1f}" for h in horizons]
        mae, rmse = score["mae"], score["rmse"]
        lines.append(
            f"| {name} | {mae['value']:.1f} ({mae['low']:.1f} to {mae['high']:.1f}) "
            f"| {rmse['value']:.1f} ({rmse['low']:.1f} to {rmse['high']:.1f}) "
            f"| {score['bias']['value']:+.1f} | " + " | ".join(cells) + " |"
        )
    cov, naive = interval["model_coverage"], interval["naive_coverage"]
    lines += [
        "",
        f"80% interval on the test fold: model coverage {cov['value']:.1%} "
        f"({cov['low']:.1%} to {cov['high']:.1%}), "
        f"mean width {interval['model_mean_width_s']:.0f} s; "
        f"persistence plus empirical residual quantiles: coverage {naive['value']:.1%} "
        f"({naive['low']:.1%} to {naive['high']:.1%}), "
        f"mean width {interval['naive_mean_width_s']:.0f} s.",
    ]
    return "\n".join(lines) + "\n"


def run_train(args: argparse.Namespace) -> int:
    events = dataset.load_events(args.events)
    print(f"{len(events)} arrivals, {len({e.trip_id for e in events})} trips", file=sys.stderr)
    samples = dataset.build_samples(events)
    folds = dataset.time_folds(events, tuple(args.fractions))
    assignment = folds.assign(samples)
    parts = {name: samples.take(assignment == i) for i, name in enumerate(dataset.FOLDS)}
    dropped = int((assignment < 0).sum())
    exclude = () if args.calendar_features else train.SMALL_DATA_EXCLUDED
    heads = train.fit(
        parts["train"], parts["early_stop"], parts["calibration"], exclude=tuple(exclude)
    )
    test = parts["test"]
    evaluation = evaluate.evaluate(heads, parts["train"], parts["calibration"], test, train.NOMINAL)
    first, last = min(e.actual_s for e in events), max(e.actual_s for e in events)
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
            "samples": len(samples),
            "dropped_straddling_a_boundary": dropped,
            "lags_in_stops": list(dataset.DEFAULT_LAGS),
            "features": heads.features,
            "rounds": heads.rounds,
            "params": train.PARAMS,
            "trained_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **evaluation,
        }
    )
    document = report.model_document(heads, card)
    report.write_json(args.model_out, document, compact=True)
    report.write_json(args.report_dir / "report.json", card)
    ranked = report.write_plots(args.report_dir, heads, evaluation, test)
    (args.report_dir / "results.md").write_text(results_table(card), encoding="utf-8")
    print(results_table(card))
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
    args = parser.parse_args()
    np.seterr(all="raise", under="ignore")
    return run_train(args)


if __name__ == "__main__":
    raise SystemExit(main())
