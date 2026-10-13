"""The portable evaluator reproduces LightGBM, checked against committed golden values.

The fixture holds ensembles trained by LightGBM and what LightGBM predicted for probe rows
(prediction and SHAP values), so this runs without LightGBM or NumPy. Regenerate it with
``python scripts/make_ml_fixtures.py`` (needs the out-of-tree training libraries).
"""

import json
import math
from pathlib import Path

import pytest

from app.ml.ensemble import Ensemble

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "ml_golden.json").read_text())
CASES = {case["name"]: case for case in GOLDEN["cases"]}


def rows(case: dict) -> list[list[float]]:
    return [[math.nan if v is None else v for v in row] for row in case["probes"]]


def test_the_fixture_covers_every_split_type_and_the_production_configuration() -> None:
    assert set(CASES) == {
        "regression",
        "quantile",
        "regression_l1",
        "nan_only_at_prediction",
        "production_configuration_early_stopped",
    }
    assert any(None in row for row in CASES["regression"]["probes"])  # NaN probes


@pytest.mark.parametrize("name", sorted(CASES))
def test_predictions_equal_lightgbm(name: str) -> None:
    case = CASES[name]
    ensemble = Ensemble.from_json(case["ensemble"], case["n_features"])
    for row, expected in zip(rows(case), case["predict"], strict=True):
        assert ensemble.predict(row) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("name", sorted(CASES))
def test_contributions_equal_lightgbm_shap_and_sum_to_the_prediction(name: str) -> None:
    case = CASES[name]
    ensemble = Ensemble.from_json(case["ensemble"], case["n_features"])
    for row, expected, shap in zip(rows(case), case["predict"], case["contrib"], strict=True):
        phi, bias = ensemble.contributions(row)
        assert [*phi, bias] == pytest.approx(shap, abs=1e-7)
        assert sum(phi) + bias == pytest.approx(expected, abs=1e-7)


def test_json_round_trip_is_lossless() -> None:
    case = CASES["regression"]
    ensemble = Ensemble.from_json(case["ensemble"], case["n_features"])
    clone = Ensemble.from_json(ensemble.to_json(), ensemble.n_features)
    row = rows(case)[3]
    assert clone.predict(row) == ensemble.predict(row)
