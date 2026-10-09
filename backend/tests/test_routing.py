"""Rerouting on a small fixture network.

Line 1 runs A-B-C-D-E (2 min per hop). Line 2 is a slower bypass A-X-Y-E (5 min per hop).
Both run every 10 minutes, so boarding costs a 5 min expected wait plus a 2 min transfer
penalty. Stops on the lines are about 700 m apart, so the only walk is from the unserved
stop W to B (~190 m).
"""

import pytest

from app.routing import (
    TRANSFER_PENALTY_S,
    WALK_DETOUR_FACTOR,
    WALK_SPEED_MPS,
    Disruption,
    GraphRoute,
    GraphStop,
    Segment,
    TransitGraph,
    analyse_disruption,
    distance_meters,
)

STOPS = {
    "A": (52.200, 21.000),
    "B": (52.200, 21.010),
    "C": (52.200, 21.020),
    "D": (52.200, 21.030),
    "E": (52.200, 21.040),
    "X": (52.210, 21.013),
    "Y": (52.210, 21.027),
    "W": (52.200, 21.0128),
}
DAILY_TRIPS = 108  # every 10 minutes for 18 hours
SERVICE_MINUTES = 18 * 60
BOARDING_S = 300 + TRANSFER_PENALTY_S


def _line(route_id: str, stops: list[str], run_seconds: int) -> list[Segment]:
    segments = []
    for direction in (stops, list(reversed(stops))):
        for from_stop, to_stop in zip(direction, direction[1:], strict=False):
            segments.append(
                Segment(route_id, from_stop, to_stop, run_seconds, DAILY_TRIPS, SERVICE_MINUTES)
            )
    return segments


@pytest.fixture
def graph() -> TransitGraph:
    return TransitGraph(
        [GraphStop(stop_id, stop_id, lat, lon) for stop_id, (lat, lon) in STOPS.items()],
        [GraphRoute("1", "1", "tram"), GraphRoute("2", "2", "bus")],
        _line("1", ["A", "B", "C", "D", "E"], 120) + _line("2", ["A", "X", "Y", "E"], 300),
    )


def test_baseline_takes_the_direct_line(graph: TransitGraph) -> None:
    journey = graph.shortest_paths("A", ["E"])["E"]

    assert journey.seconds == BOARDING_S + 4 * 120
    assert [(leg.kind, leg.route_id, leg.from_stop, leg.to_stop) for leg in journey.legs] == [
        ("ride", "1", "A", "E")
    ]
    assert journey.legs[0].stops == 4
    assert journey.legs[0].wait_seconds == BOARDING_S


def test_stop_closure_removes_edges_and_reroutes_via_the_bypass(graph: TransitGraph) -> None:
    impact = analyse_disruption(graph, Disruption("closure", stop_ids=frozenset({"C"})), 30)

    assert impact.affected_route_ids == ("1",)
    assert impact.affected_segments == 4  # B-C and C-D in both directions
    detour = next(item for item in impact.detours if (item.origin, item.destination) == ("A", "E"))
    assert detour.disrupted is not None
    assert [leg.route_id for leg in detour.disrupted.legs] == ["2"]
    assert detour.added_seconds == (BOARDING_S + 900) - (BOARDING_S + 480)
    assert impact.average_added_minutes == 7.0
    assert impact.unreachable_pairs == 0


def test_slowdown_scales_run_times_without_removing_the_line(graph: TransitGraph) -> None:
    impact = analyse_disruption(
        graph, Disruption("slowdown", stop_ids=frozenset({"C"}), slowdown_factor=2.0), 30
    )

    detour = next(item for item in impact.detours if (item.origin, item.destination) == ("A", "E"))
    assert detour.disrupted is not None
    assert [leg.route_id for leg in detour.disrupted.legs] == ["1"]
    assert detour.added_seconds == 2 * 120  # B-C and C-D each take one extra hop time


def test_severe_slowdown_switches_to_a_faster_alternative(graph: TransitGraph) -> None:
    impact = analyse_disruption(
        graph, Disruption("slowdown", stop_ids=frozenset({"C"}), slowdown_factor=4.0), 30
    )

    detour = next(item for item in impact.detours if (item.origin, item.destination) == ("A", "E"))
    assert detour.disrupted is not None
    assert [leg.route_id for leg in detour.disrupted.legs] == ["2"]
    assert detour.added_seconds == 420


def test_line_closure_reports_unreachable_journeys(graph: TransitGraph) -> None:
    impact = analyse_disruption(graph, Disruption("closure", route_ids=frozenset({"1"})), 60)

    assert impact.affected_route_ids == ("1",)
    assert impact.affected_segments == 8
    # Line 1 has five stops, so every stop is sampled: four hops in each direction. Only
    # hops whose ends are served by line 2 (none of them) or walkable could be rerouted.
    assert len(impact.detours) == 8
    assert impact.unreachable_pairs == 8
    assert impact.average_added_minutes is None


def test_transfers_pay_a_wait_and_a_penalty_on_each_boarding() -> None:
    graph = TransitGraph(
        [GraphStop(stop_id, stop_id, lat, lon) for stop_id, (lat, lon) in STOPS.items()],
        [GraphRoute("1", "1", "tram"), GraphRoute("3", "3", "bus")],
        _line("1", ["A", "B", "C"], 120) + _line("3", ["C", "D"], 120),
    )

    journey = graph.shortest_paths("A", ["D"])["D"]

    assert [leg.route_id for leg in journey.legs] == ["1", "3"]
    assert journey.seconds == 2 * BOARDING_S + 3 * 120


def test_walking_links_nearby_stops(graph: TransitGraph) -> None:
    journey = graph.shortest_paths("W", ["E"])["E"]
    walk_seconds = round(
        distance_meters(*STOPS["W"], *STOPS["B"]) * WALK_DETOUR_FACTOR / WALK_SPEED_MPS
    )

    assert [leg.kind for leg in journey.legs] == ["walk", "ride"]
    assert journey.legs[0].to_stop == "B"
    assert journey.seconds == walk_seconds + BOARDING_S + 3 * 120
    assert "C" not in {other for other, _ in graph.walks["B"]}


def test_affected_trips_scale_with_duration(graph: TransitGraph) -> None:
    short = analyse_disruption(graph, Disruption("closure", stop_ids=frozenset({"C"})), 30)
    long = analyse_disruption(graph, Disruption("closure", stop_ids=frozenset({"C"})), 120)

    # 108 trips a day per direction over 18 hours: 6 per hour per direction, both pass C.
    assert short.affected_trips == 6
    assert long.affected_trips == 24
    assert short.trips_by_route == {"1": 6}


def test_route_chains_follow_each_direction(graph: TransitGraph) -> None:
    chains = graph.route_chains("1")

    assert sorted(chains) == [["A", "B", "C", "D", "E"], ["E", "D", "C", "B", "A"]]
