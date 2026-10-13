"""Regenerate the committed ML test fixtures (needs numpy, scipy and lightgbm, installed by hand).

    cd backend && python scripts/make_ml_fixtures.py

Writes two small JSON files to tests/fixtures/:

* ml_golden.json: tree ensembles trained by LightGBM, probe rows (``null`` is NaN) and what
  LightGBM predicts for them, prediction and SHAP values. tests/test_ml_ensemble_golden.py checks
  that the pure-Python evaluator reproduces them, so CI needs no LightGBM.
* ml_tiny_delay_model.json: a small, complete delay model file (trained on synthetic arrivals)
  that the server-side tests serve.

Only run it when the model format or the feature list changes; commit the result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ml import dataset, report, train  # noqa: E402
from ml.export import ensemble_from_booster  # noqa: E402
from ml.synthetic import BASE, synthetic_events  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BASE_PARAMS = {
    "num_leaves": 12,
    "learning_rate": 0.1,
    "min_data_in_leaf": 20,
    "verbose": -1,
    "seed": 1,
    "deterministic": True,
    "num_threads": 1,
}


def data(rows: int = 3000, seed: int = 3):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(rows, 6))
    x[:, 3] = np.round(x[:, 3])  # few distinct values
    x[rng.random(x.shape) < 0.15] = np.nan  # missing values in every column
    y = (
        2 * np.nan_to_num(x[:, 0])
        - np.nan_to_num(x[:, 1]) * (np.nan_to_num(x[:, 2]) > 0)
        + 0.5 * np.nan_to_num(x[:, 3])
        + rng.normal(scale=0.3, size=rows)
    )
    return x, y


def case(name: str, booster: lgb.Booster, probe: np.ndarray) -> dict:
    ensemble = ensemble_from_booster(booster)
    return {
        "name": name,
        "n_features": ensemble.n_features,
        "ensemble": ensemble.to_json(),
        "probes": [[None if np.isnan(v) else float(v) for v in row] for row in probe],
        "predict": booster.predict(probe).tolist(),
        "contrib": booster.predict(probe, pred_contrib=True).tolist(),
    }


def golden() -> dict:
    x, y = data()
    cases = []
    for objective, extra in (
        ("regression", {}),
        ("quantile", {"alpha": 0.8}),
        ("regression_l1", {}),
    ):
        booster = lgb.train(
            {**BASE_PARAMS, "objective": objective, **extra}, lgb.Dataset(x, y), num_boost_round=12
        )
        cases.append(case(objective, booster, x[:25]))

    unseen = x.copy()
    unseen[:, 4] = np.nan_to_num(unseen[:, 4])  # column 4 never missing in training
    booster = lgb.train(
        {**BASE_PARAMS, "objective": "regression"}, lgb.Dataset(unseen, y), num_boost_round=12
    )
    probe = unseen[:25].copy()
    probe[:, 4] = np.nan  # missing only at prediction time
    cases.append(case("nan_only_at_prediction", booster, probe))

    x, y = data(6000, seed=11)
    cut = 4500
    params = {
        **BASE_PARAMS,
        "objective": "quantile",
        "alpha": 0.9,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.7,
        "bagging_freq": 1,
        "lambda_l2": 10.0,
    }
    fit = lgb.Dataset(x[:cut], y[:cut])
    booster = lgb.train(
        params,
        fit,
        400,
        valid_sets=[lgb.Dataset(x[cut:], y[cut:], reference=fit)],
        callbacks=[lgb.early_stopping(15, verbose=False)],
    )
    assert 0 < booster.best_iteration < 400
    cases.append(case("production_configuration_early_stopped", booster, x[cut : cut + 25]))
    return {"lightgbm": lgb.__version__, "cases": cases}


def tiny_model() -> dict:
    events = synthetic_events()
    samples = dataset.build_samples(events)
    fold = dataset.time_folds(events).assign(samples)
    parts = [samples.take(fold == i) for i in range(4)]
    heads = train.fit(parts[0], parts[1], parts[2], exclude=())
    card = {
        "data": {"last_event_epoch": BASE},
        "interval": {"nominal": 0.8},
        "promoted": True,
        "note": "synthetic test model, not a real result",
    }
    serving_range = {"max_stops_ahead": 12, "max_sched_run_s": 1e9, "max_origin_age_s": 600}
    return report.model_document(heads, card, serving_range)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "ml_golden.json").write_text(json.dumps(golden(), separators=(",", ":")) + "\n")
    report.write_json(FIXTURES / "ml_tiny_delay_model.json", tiny_model(), compact=True)
    for name in ("ml_golden.json", "ml_tiny_delay_model.json"):
        print(name, (FIXTURES / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
