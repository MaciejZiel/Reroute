"""Tests for geographic substitute-stop analysis."""

from types import SimpleNamespace

from app.api import _distance_meters, _rank_nearby_stops


def _stop(stop_id: str, name: str, latitude: float, longitude: float) -> SimpleNamespace:
    return SimpleNamespace(id=stop_id, name=name, latitude=latitude, longitude=longitude)


def test_distance_uses_warsaw_scale() -> None:
    distance = _distance_meters(52.23, 21.01, 52.23, 21.02)

    assert 670 < distance < 690


def test_nearby_stop_suggestions_are_sorted_and_limited_to_500_meters() -> None:
    target = _stop("target", "Centrum", 52.23, 21.01)
    nearer = _stop("nearer", "Nearby A", 52.23, 21.011)
    farther = _stop("farther", "Nearby B", 52.23, 21.013)
    outside = _stop("outside", "Too far", 52.23, 21.02)

    suggestions = _rank_nearby_stops(target, [farther, outside, nearer])

    assert [stop.id for stop, _ in suggestions] == ["nearer", "farther"]
    assert all(distance <= 500 for _, distance in suggestions)


def test_nearby_stop_suggestions_exclude_the_target_and_respect_limit() -> None:
    target = _stop("target", "Centrum", 52.23, 21.01)
    candidates = [
        _stop(str(index), f"Stop {index}", 52.23 + index * 0.0001, 21.01) for index in range(1, 9)
    ]

    suggestions = _rank_nearby_stops(target, [target, *candidates], limit=5)

    assert len(suggestions) == 5
    assert all(stop.id != target.id for stop, _ in suggestions)
