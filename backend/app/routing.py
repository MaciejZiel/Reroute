"""Shortest paths on the stop graph and disruption impact analysis.

The network is modelled as a route-expanded graph. A state is either "standing at a stop"
(``route_id == ""``) or "on board route R at a stop". Edges are:

* walking between stops within ``WALK_RADIUS_M`` (street detour factor applied),
* boarding a route at a stop (expected wait from the route's frequency plus a fixed
  transfer penalty),
* riding one scheduled segment to the next stop (median scheduled run time),
* alighting (free).

A closure removes every ride segment touching the disrupted stop or belonging to the
disrupted line; a slowdown multiplies their run times. Rerouting is plain Dijkstra over
this graph, run from a handful of origin stops sampled around the disruption.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from statistics import mean
from typing import Literal

WALK_RADIUS_M = 400
WALK_SPEED_MPS = 1.2
WALK_DETOUR_FACTOR = 1.3
TRANSFER_PENALTY_S = 120
MIN_WAIT_S = 60
MAX_WAIT_S = 900
MAX_SEARCH_S = 3 * 3600
STOPS_AROUND_DISRUPTION = 2
SAMPLED_STOPS_PER_ROUTE = 5

DisruptionKind = Literal["closure", "slowdown"]


@dataclass(frozen=True)
class GraphStop:
    id: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class GraphRoute:
    id: str
    label: str
    mode: str


@dataclass(frozen=True)
class Segment:
    """One scheduled hop of a route between two consecutive stops."""

    route_id: str
    from_stop: str
    to_stop: str
    run_seconds: int
    trips: int
    service_minutes: int


@dataclass(frozen=True)
class Disruption:
    kind: DisruptionKind
    stop_ids: frozenset[str] = frozenset()
    route_ids: frozenset[str] = frozenset()
    slowdown_factor: float = 1.5

    def touches(self, segment: Segment) -> bool:
        return (
            segment.route_id in self.route_ids
            or segment.from_stop in self.stop_ids
            or segment.to_stop in self.stop_ids
        )


@dataclass(frozen=True)
class Leg:
    kind: Literal["ride", "walk"]
    from_stop: str
    to_stop: str
    seconds: int
    route_id: str = ""
    wait_seconds: int = 0
    stops: int = 0


@dataclass(frozen=True)
class Journey:
    seconds: int
    legs: tuple[Leg, ...]


@dataclass(frozen=True)
class Detour:
    route_ids: tuple[str, ...]
    origin: str
    destination: str
    baseline: Journey
    disrupted: Journey | None

    @property
    def added_seconds(self) -> int | None:
        if self.disrupted is None:
            return None
        return max(self.disrupted.seconds - self.baseline.seconds, 0)


@dataclass(frozen=True)
class DisruptionImpact:
    affected_route_ids: tuple[str, ...]
    affected_segments: int
    affected_trips: int
    detours: tuple[Detour, ...]
    trips_by_route: Mapping[str, int] = field(default_factory=dict)

    @property
    def reachable(self) -> list[Detour]:
        return [detour for detour in self.detours if detour.disrupted is not None]

    @property
    def average_added_minutes(self) -> float | None:
        added = [detour.added_seconds or 0 for detour in self.reachable]
        return round(mean(added) / 60, 1) if added else None

    @property
    def max_added_minutes(self) -> float | None:
        added = [detour.added_seconds or 0 for detour in self.reachable]
        return round(max(added) / 60, 1) if added else None

    @property
    def unreachable_pairs(self) -> int:
        return sum(detour.disrupted is None for detour in self.detours)


# A search state: (stop id, route id or "" when standing at the stop).
State = tuple[str, str]


class TransitGraph:
    """Immutable stop graph built once from the timetable and reused across queries."""

    def __init__(
        self,
        stops: Iterable[GraphStop],
        routes: Iterable[GraphRoute],
        segments: Iterable[Segment],
        walk_radius_m: float = WALK_RADIUS_M,
    ) -> None:
        self.stops: dict[str, GraphStop] = {stop.id: stop for stop in stops}
        self.routes: dict[str, GraphRoute] = {route.id: route for route in routes}
        self.segments_from: dict[str, list[Segment]] = defaultdict(list)
        self.segments_by_route: dict[str, list[Segment]] = defaultdict(list)
        self.segments_by_stop: dict[str, list[Segment]] = defaultdict(list)
        for segment in segments:
            if (
                segment.route_id not in self.routes
                or segment.from_stop not in self.stops
                or segment.to_stop not in self.stops
                or segment.from_stop == segment.to_stop
            ):
                continue
            self.segments_from[segment.from_stop].append(segment)
            self.segments_by_route[segment.route_id].append(segment)
            self.segments_by_stop[segment.from_stop].append(segment)
            self.segments_by_stop[segment.to_stop].append(segment)
        self.board_wait = self._board_waits()
        self.walks = self._walking_edges(walk_radius_m)

    @property
    def segment_count(self) -> int:
        return sum(len(items) for items in self.segments_by_route.values())

    def _board_waits(self) -> dict[State, int]:
        # Frequency of the busiest direction leaving the stop; the two directions of a line
        # are separate segments, so summing them would halve the expected wait.
        trips: dict[State, int] = defaultdict(int)
        span: dict[State, int] = defaultdict(int)
        for stop_id, segments in self.segments_from.items():
            for segment in segments:
                key = (stop_id, segment.route_id)
                if segment.trips > trips[key]:
                    trips[key] = segment.trips
                    span[key] = segment.service_minutes
        waits = {}
        for key, count in trips.items():
            headway = span[key] * 60 / count if count else MAX_WAIT_S * 2
            waits[key] = int(min(max(headway / 2, MIN_WAIT_S), MAX_WAIT_S))
        return waits

    def _walking_edges(self, radius_m: float) -> dict[str, list[tuple[str, int]]]:
        """Connect stops within walking distance, using a lat/lon grid instead of n² pairs."""
        cell_lat = radius_m / 111_000
        mean_lat = mean(stop.latitude for stop in self.stops.values()) if self.stops else 52.0
        cell_lon = cell_lat / max(math.cos(math.radians(mean_lat)), 0.1)
        grid: dict[tuple[int, int], list[GraphStop]] = defaultdict(list)
        for stop in self.stops.values():
            grid[(int(stop.latitude // cell_lat), int(stop.longitude // cell_lon))].append(stop)

        walks: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for (row, column), members in grid.items():
            neighbours = [
                other
                for d_row in (-1, 0, 1)
                for d_column in (-1, 0, 1)
                for other in grid.get((row + d_row, column + d_column), ())
            ]
            for stop in members:
                for other in neighbours:
                    if other.id == stop.id:
                        continue
                    distance = distance_meters(
                        stop.latitude, stop.longitude, other.latitude, other.longitude
                    )
                    if distance <= radius_m:
                        seconds = round(distance * WALK_DETOUR_FACTOR / WALK_SPEED_MPS)
                        walks[stop.id].append((other.id, max(seconds, 30)))
        return walks

    def route_chains(self, route_id: str) -> list[list[str]]:
        """Main stop sequence per direction: follow the busiest successor from each terminal."""
        segments = self.segments_by_route.get(route_id, [])
        successors: dict[str, list[Segment]] = defaultdict(list)
        neighbours: dict[str, set[str]] = defaultdict(set)
        has_predecessor: set[str] = set()
        for segment in segments:
            successors[segment.from_stop].append(segment)
            has_predecessor.add(segment.to_stop)
            neighbours[segment.from_stop].add(segment.to_stop)
            neighbours[segment.to_stop].add(segment.from_stop)
        # Terminals: stops never arrived at (one-way patterns) or with a single neighbour
        # (where a two-way line turns around).
        terminals = {
            stop_id
            for stop_id in successors
            if stop_id not in has_predecessor or len(neighbours[stop_id]) == 1
        }
        starts = sorted(
            terminals,
            key=lambda stop_id: (-sum(item.trips for item in successors[stop_id]), stop_id),
        )
        if not starts and segments:
            starts = [max(segments, key=lambda item: item.trips).from_stop]
        chains = []
        for start in starts[:2]:
            chain, seen, current = [start], {start}, start
            while True:
                options = [item for item in successors[current] if item.to_stop not in seen]
                if not options:
                    break
                following = max(options, key=lambda item: (item.trips, item.to_stop))
                chain.append(following.to_stop)
                seen.add(following.to_stop)
                current = following.to_stop
            if len(chain) >= 2:
                chains.append(chain)
        return chains

    def shortest_paths(
        self,
        origin: str,
        targets: Iterable[str],
        disruption: Disruption | None = None,
        max_seconds: int = MAX_SEARCH_S,
    ) -> dict[str, Journey]:
        """Dijkstra from one origin stop; stops early once every target is settled."""
        remaining = {target for target in targets if target in self.stops and target != origin}
        found: dict[str, Journey] = {}
        if origin not in self.stops or not remaining:
            return found

        start: State = (origin, "")
        best: dict[State, int] = {start: 0}
        previous: dict[State, tuple[State, str, int]] = {}
        queue: list[tuple[int, str, str]] = [(0, origin, "")]
        while queue and remaining:
            cost, stop_id, route_id = heapq.heappop(queue)
            state = (stop_id, route_id)
            if cost > best.get(state, math.inf) or cost > max_seconds:
                continue
            if not route_id and stop_id in remaining:
                remaining.discard(stop_id)
                found[stop_id] = Journey(cost, self._legs(previous, state))
            for next_state, edge, weight in self._edges(state, disruption):
                next_cost = cost + weight
                if next_cost < best.get(next_state, math.inf):
                    best[next_state] = next_cost
                    previous[next_state] = (state, edge, weight)
                    heapq.heappush(queue, (next_cost, next_state[0], next_state[1]))
        return found

    def _edges(
        self, state: State, disruption: Disruption | None
    ) -> Iterable[tuple[State, str, int]]:
        stop_id, route_id = state
        if not route_id:
            for other, seconds in self.walks.get(stop_id, ()):
                yield (other, ""), "walk", seconds
            boarded: set[str] = set()
            for segment in self.segments_from.get(stop_id, ()):
                if segment.route_id in boarded or self._weight(segment, disruption) is None:
                    continue
                boarded.add(segment.route_id)
                wait = self.board_wait.get((stop_id, segment.route_id), MAX_WAIT_S)
                yield (stop_id, segment.route_id), "board", wait + TRANSFER_PENALTY_S
            return
        yield (stop_id, ""), "alight", 0
        for segment in self.segments_from.get(stop_id, ()):
            if segment.route_id != route_id:
                continue
            weight = self._weight(segment, disruption)
            if weight is not None:
                yield (segment.to_stop, route_id), "ride", weight

    @staticmethod
    def _weight(segment: Segment, disruption: Disruption | None) -> int | None:
        if disruption is None or not disruption.touches(segment):
            return segment.run_seconds
        if disruption.kind == "closure":
            return None
        return round(segment.run_seconds * disruption.slowdown_factor)

    @staticmethod
    def _legs(previous: dict[State, tuple[State, str, int]], state: State) -> tuple[Leg, ...]:
        steps: list[tuple[State, State, str, int]] = []
        while state in previous:
            before, edge, weight = previous[state]
            steps.append((before, state, edge, weight))
            state = before
        steps.reverse()

        legs: list[Leg] = []
        pending_wait = 0
        for before, after, edge, weight in steps:
            if edge == "board":
                pending_wait = weight
            elif edge == "ride":
                last = legs[-1] if legs else None
                if last and last.kind == "ride" and last.route_id == after[1] and not pending_wait:
                    legs[-1] = Leg(
                        "ride",
                        last.from_stop,
                        after[0],
                        last.seconds + weight,
                        last.route_id,
                        last.wait_seconds,
                        last.stops + 1,
                    )
                else:
                    legs.append(Leg("ride", before[0], after[0], weight, after[1], pending_wait, 1))
                pending_wait = 0
            elif edge == "walk":
                last = legs[-1] if legs else None
                if last and last.kind == "walk":
                    legs[-1] = Leg("walk", last.from_stop, after[0], last.seconds + weight)
                else:
                    legs.append(Leg("walk", before[0], after[0], weight))
        return tuple(legs)


def analyse_disruption(
    graph: TransitGraph, disruption: Disruption, duration_minutes: int
) -> DisruptionImpact:
    """Find the disrupted segments, the trips they carry and the detours passengers face."""
    affected = [
        segment
        for segments in graph.segments_by_route.values()
        for segment in segments
        if disruption.touches(segment)
    ]
    route_ids = tuple(sorted({segment.route_id for segment in affected}))
    trips_by_route = {
        route_id: _trips_in_window(
            [segment for segment in affected if segment.route_id == route_id], duration_minutes
        )
        for route_id in route_ids
    }

    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route_id in route_ids:
        for chain in graph.route_chains(route_id):
            for origin, destination in _sample_pairs(chain, disruption, route_id):
                if route_id not in pairs[(origin, destination)]:
                    pairs[(origin, destination)].append(route_id)

    by_origin: dict[str, list[str]] = defaultdict(list)
    for origin, destination in pairs:
        by_origin[origin].append(destination)

    detours = []
    for origin, destinations in by_origin.items():
        baseline = graph.shortest_paths(origin, destinations)
        disrupted = graph.shortest_paths(origin, destinations, disruption)
        for destination in destinations:
            if destination not in baseline:
                continue
            detours.append(
                Detour(
                    route_ids=tuple(pairs[(origin, destination)]),
                    origin=origin,
                    destination=destination,
                    baseline=baseline[destination],
                    disrupted=disrupted.get(destination),
                )
            )
    detours.sort(
        key=lambda detour: (
            detour.disrupted is not None,
            -(detour.added_seconds or 0),
            detour.origin,
        )
    )
    return DisruptionImpact(
        affected_route_ids=route_ids,
        affected_segments=len(affected),
        affected_trips=sum(trips_by_route.values()),
        detours=tuple(detours),
        trips_by_route=trips_by_route,
    )


def _sample_pairs(chain: list[str], disruption: Disruption, route_id: str) -> list[tuple[str, str]]:
    """Origin/destination pairs on one direction of an affected route."""
    if route_id in disruption.route_ids:
        if len(chain) <= SAMPLED_STOPS_PER_ROUTE:
            sampled = chain
        else:
            step = (len(chain) - 1) / (SAMPLED_STOPS_PER_ROUTE - 1)
            sampled = [chain[round(index * step)] for index in range(SAMPLED_STOPS_PER_ROUTE)]
        return list(zip(sampled, sampled[1:], strict=False))

    pairs = []
    for index, stop_id in enumerate(chain):
        if stop_id not in disruption.stop_ids:
            continue
        before = chain[max(index - STOPS_AROUND_DISRUPTION, 0) : index]
        after = chain[index + 1 : index + 1 + STOPS_AROUND_DISRUPTION]
        before = [item for item in before if item not in disruption.stop_ids]
        after = [item for item in after if item not in disruption.stop_ids]
        if before and after:
            pairs.append((before[0], after[-1]))
    return pairs


def _trips_in_window(segments: list[Segment], duration_minutes: int) -> int:
    """Scheduled trips crossing the disrupted part of one route during the disruption."""
    if not segments:
        return 0
    through_stop: dict[str, int] = defaultdict(int)
    for segment in segments:
        through_stop[f"in:{segment.to_stop}"] += segment.trips
        through_stop[f"out:{segment.from_stop}"] += segment.trips
    daily_trips = max(through_stop.values())
    service_minutes = max(max(segment.service_minutes for segment in segments), 60)
    return math.ceil(daily_trips * min(duration_minutes, service_minutes) / service_minutes)


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(haversine))
