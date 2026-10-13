"""Evaluate an exported gradient-boosted tree ensemble without any third-party library.

The trees are stored as flat arrays (one index space for splits and leaves) so the file stays
small and a prediction is a handful of list lookups per tree. ``contributions`` computes exact
path-dependent TreeSHAP values, the same quantity LightGBM returns for ``pred_contrib=True``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Missing-value handling per split, as LightGBM defines it (its ``zero_as_missing`` is unused).
MISSING_NONE = 0  # NaN is treated as 0.0
MISSING_NAN = 1  # NaN follows the default direction


@dataclass(frozen=True, slots=True)
class Tree:
    feature: list[int]  # -1 for a leaf
    threshold: list[float]
    left: list[int]
    right: list[int]
    default_left: list[bool]
    missing: list[int]
    value: list[float]  # leaf output; unused for splits
    cover: list[int]  # training rows that reached the node

    @classmethod
    def from_json(cls, data: dict) -> Tree:
        return cls(
            feature=data["f"],
            threshold=data["t"],
            left=data["l"],
            right=data["r"],
            default_left=[bool(code & 1) for code in data["d"]],
            missing=[code >> 1 for code in data["d"]],
            value=data["v"],
            cover=data["c"],
        )

    def to_json(self) -> dict:
        return {
            "f": self.feature,
            "t": self.threshold,
            "l": self.left,
            "r": self.right,
            "d": [
                (m << 1) | int(dl) for m, dl in zip(self.missing, self.default_left, strict=True)
            ],
            "v": self.value,
            "c": self.cover,
        }

    def goes_left(self, node: int, x: Sequence[float]) -> bool:
        value = x[self.feature[node]]
        if math.isnan(value):
            if self.missing[node] == MISSING_NAN:
                return self.default_left[node]
            value = 0.0
        return value <= self.threshold[node]

    def predict(self, x: Sequence[float]) -> float:
        node = 0
        while self.feature[node] >= 0:
            node = self.left[node] if self.goes_left(node, x) else self.right[node]
        return self.value[node]

    def expected_value(self) -> float:
        total = 0.0
        stack = [0]
        while stack:
            node = stack.pop()
            if self.feature[node] < 0:
                total += self.value[node] * self.cover[node]
            else:
                stack += [self.left[node], self.right[node]]
        return total / self.cover[0]

    def shap(self, x: Sequence[float], phi: list[float]) -> None:
        """Add this tree's TreeSHAP values for ``x`` to ``phi`` (path-dependent algorithm)."""
        self._recurse(0, x, phi, [], 1.0, 1.0, -1)

    def _recurse(
        self,
        node: int,
        x: Sequence[float],
        phi: list[float],
        parent_path: list[list[float]],
        zero_fraction: float,
        one_fraction: float,
        feature: int,
    ) -> None:
        path = [element[:] for element in parent_path]
        _extend(path, zero_fraction, one_fraction, feature)
        if self.feature[node] < 0:
            for i in range(1, len(path)):
                weight = _unwound_sum(path, i)
                element = path[i]
                phi[int(element[0])] += weight * (element[2] - element[1]) * self.value[node]
            return
        split = self.feature[node]
        hot, cold = (
            (self.left[node], self.right[node])
            if self.goes_left(node, x)
            else (self.right[node], self.left[node])
        )
        incoming_zero = incoming_one = 1.0
        for k in range(1, len(path)):
            if int(path[k][0]) == split:
                incoming_zero, incoming_one = path[k][1], path[k][2]
                _unwind(path, k)
                break
        cover = self.cover[node]
        self._recurse(
            hot, x, phi, path, incoming_zero * self.cover[hot] / cover, incoming_one, split
        )
        self._recurse(
            cold, x, phi, path, incoming_zero * self.cover[cold] / cover, 0.0, split
        )


# A path element is [feature, zero_fraction, one_fraction, weight].
def _extend(path: list[list[float]], zero: float, one: float, feature: int) -> None:
    depth = len(path)
    path.append([feature, zero, one, 1.0 if depth == 0 else 0.0])
    for i in range(depth - 1, -1, -1):
        path[i + 1][3] += one * path[i][3] * (i + 1) / (depth + 1)
        path[i][3] = zero * path[i][3] * (depth - i) / (depth + 1)


def _unwind(path: list[list[float]], index: int) -> None:
    depth = len(path) - 1
    one, zero = path[index][2], path[index][1]
    following = path[depth][3]
    for j in range(depth - 1, -1, -1):
        if one != 0:
            current = path[j][3]
            path[j][3] = following * (depth + 1) / ((j + 1) * one)
            following = current - path[j][3] * zero * (depth - j) / (depth + 1)
        else:
            path[j][3] = path[j][3] * (depth + 1) / (zero * (depth - j))
    for j in range(index, depth):
        path[j][:3] = path[j + 1][:3]
    path.pop()


def _unwound_sum(path: list[list[float]], index: int) -> float:
    depth = len(path) - 1
    one, zero = path[index][2], path[index][1]
    following = path[depth][3]
    total = 0.0
    for j in range(depth - 1, -1, -1):
        if one != 0:
            tmp = following * (depth + 1) / ((j + 1) * one)
            total += tmp
            following = path[j][3] - tmp * zero * (depth - j) / (depth + 1)
        else:
            total += path[j][3] / zero / ((depth - j) / (depth + 1))
    return total


class Ensemble:
    """A sum of trees: ``predict`` is the raw score, ``contributions`` its per-feature split."""

    def __init__(self, trees: list[Tree], n_features: int) -> None:
        self.trees = trees
        self.n_features = n_features
        self._bias = sum(tree.expected_value() for tree in trees)

    @classmethod
    def from_json(cls, data: dict, n_features: int) -> Ensemble:
        return cls([Tree.from_json(tree) for tree in data["trees"]], n_features)

    def to_json(self) -> dict:
        return {"trees": [tree.to_json() for tree in self.trees]}

    def predict(self, x: Sequence[float]) -> float:
        return sum(tree.predict(x) for tree in self.trees)

    def contributions(self, x: Sequence[float]) -> tuple[list[float], float]:
        """Per-feature SHAP values and the bias; they sum to ``predict(x)``."""
        phi = [0.0] * self.n_features
        for tree in self.trees:
            tree.shap(x, phi)
        return phi, self._bias
