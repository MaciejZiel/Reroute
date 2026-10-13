"""The exporter and evaluator against a live LightGBM (needs the out-of-tree libraries).

The same equivalence is pinned without LightGBM in test_ml_ensemble_golden.py."""

import math

import pytest

REASON = "training libraries are installed out of tree (see README)"
np = pytest.importorskip("numpy", reason=REASON)
lgb = pytest.importorskip("lightgbm", reason=REASON)

from app.ml.ensemble import Ensemble  # noqa: E402
from ml.export import ensemble_from_booster  # noqa: E402


def _data(rows: int = 3000, seed: int = 3):
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


def _booster(objective: str, x, y):
    params = {
        "objective": objective,
        "num_leaves": 12,
        "learning_rate": 0.1,
        "min_data_in_leaf": 20,
        "verbose": -1,
        "seed": 1,
        "deterministic": True,
        "num_threads": 1,
    }
    if objective == "quantile":
        params["alpha"] = 0.8
    return lgb.train(params, lgb.Dataset(x, y), num_boost_round=40)


@pytest.mark.parametrize("objective", ["regression", "quantile", "regression_l1"])
def test_exported_ensemble_matches_lightgbm_predictions(objective: str) -> None:
    x, y = _data()
    booster = _booster(objective, x, y)
    ensemble = ensemble_from_booster(booster)
    probe = x[:400]
    expected = booster.predict(probe)
    got = np.array([ensemble.predict(row.tolist()) for row in probe])
    assert np.allclose(got, expected, rtol=0, atol=1e-9)


def test_unseen_nan_columns_follow_lightgbm_rules() -> None:
    x, y = _data()
    x[:, 4] = np.nan_to_num(x[:, 4])  # column 4 never missing in training
    booster = _booster("regression", x, y)
    ensemble = ensemble_from_booster(booster)
    probe = x[:50].copy()
    probe[:, 4] = np.nan  # missing only at prediction time
    expected = booster.predict(probe)
    got = np.array([ensemble.predict(row.tolist()) for row in probe])
    assert np.allclose(got, expected, atol=1e-9)


def test_contributions_match_lightgbm_shap_and_sum_to_the_prediction() -> None:
    x, y = _data()
    booster = _booster("regression", x, y)
    ensemble = ensemble_from_booster(booster)
    probe = x[:60]
    reference = booster.predict(probe, pred_contrib=True)
    for row, expected in zip(probe, reference, strict=True):
        phi, bias = ensemble.contributions(row.tolist())
        assert np.allclose(phi + [bias], expected, atol=1e-7)
        assert math.isclose(sum(phi) + bias, ensemble.predict(row.tolist()), abs_tol=1e-7)


def test_json_round_trip_is_lossless() -> None:
    x, y = _data(800)
    ensemble = ensemble_from_booster(_booster("regression", x, y))
    clone = Ensemble.from_json(ensemble.to_json(), ensemble.n_features)
    row = x[7].tolist()
    assert clone.predict(row) == ensemble.predict(row)


def test_exported_ensemble_matches_lightgbm_in_the_production_configuration() -> None:
    """Quantile heads with bagging, feature subsampling and early stopping, as ml/train.py fits."""
    x, y = _data(6000, seed=11)
    cut = 4500
    params = {
        "objective": "quantile",
        "alpha": 0.9,
        "num_leaves": 15,
        "learning_rate": 0.1,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.7,
        "bagging_freq": 1,
        "lambda_l2": 10.0,
        "verbose": -1,
        "seed": 1,
        "deterministic": True,
        "num_threads": 1,
    }
    data = lgb.Dataset(x[:cut], y[:cut])
    valid = lgb.Dataset(x[cut:], y[cut:], reference=data)
    booster = lgb.train(
        params, data, 400, valid_sets=[valid], callbacks=[lgb.early_stopping(15, verbose=False)]
    )
    assert 0 < booster.best_iteration < 400  # early stopping really cut the ensemble
    ensemble = ensemble_from_booster(booster)
    probe = x[cut : cut + 80]
    expected = booster.predict(probe)
    contrib = booster.predict(probe, pred_contrib=True)
    for row, value, shap in zip(probe, expected, contrib, strict=True):
        assert ensemble.predict(row.tolist()) == pytest.approx(value, abs=1e-9)
        phi, bias = ensemble.contributions(row.tolist())
        assert np.allclose([*phi, bias], shap, atol=1e-7)
