"""Convert a trained LightGBM booster into the portable ensemble the server evaluates."""

from __future__ import annotations

from app.ml.ensemble import MISSING_NAN, MISSING_NONE, Ensemble, Tree

_MISSING = {"None": MISSING_NONE, "NaN": MISSING_NAN}


def tree_from_lightgbm(structure: dict) -> Tree:
    feature: list[int] = []
    threshold: list[float] = []
    left: list[int] = []
    right: list[int] = []
    default_left: list[bool] = []
    missing: list[int] = []
    value: list[float] = []
    cover: list[int] = []

    def add(node: dict) -> int:
        index = len(feature)
        for column in (feature, left, right, cover, missing):
            column.append(0)
        threshold.append(0.0)
        default_left.append(False)
        value.append(0.0)
        if "leaf_value" in node:
            feature[index] = -1
            left[index] = right[index] = -1
            value[index] = float(node["leaf_value"])
            cover[index] = int(node["leaf_count"])
            return index
        if node["decision_type"] != "<=":
            raise ValueError(f"unsupported split type {node['decision_type']}")
        if node["missing_type"] not in _MISSING:
            raise ValueError(f"unsupported missing type {node['missing_type']}")
        feature[index] = int(node["split_feature"])
        threshold[index] = float(node["threshold"])
        default_left[index] = bool(node["default_left"])
        missing[index] = _MISSING[node["missing_type"]]
        cover[index] = int(node["internal_count"])
        left[index] = add(node["left_child"])
        right[index] = add(node["right_child"])
        return index

    add(structure)
    return Tree(feature, threshold, left, right, default_left, missing, value, cover)


def ensemble_from_booster(booster) -> Ensemble:
    dump = booster.dump_model()
    trees = [tree_from_lightgbm(info["tree_structure"]) for info in dump["tree_info"]]
    return Ensemble(trees, int(dump["max_feature_idx"]) + 1)
