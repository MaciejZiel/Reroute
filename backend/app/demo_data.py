"""Small fictional network used when live sources are unavailable."""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, Point
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import DelaySnapshot, Route, RouteStop, Stop, VehiclePosition

STOPS = [
    ("centrum", "Centrum", 52.2298, 21.0118),
    ("politechnika", "Politechnika", 52.2209, 21.0108),
    ("pole-mokotowskie", "Pole Mokotowskie", 52.2145, 21.0073),
    ("raclawicka", "Racławicka", 52.2087, 21.0068),
    ("wierzbno", "Wierzbno", 52.1958, 21.0091),
    ("wil-anowska", "Wilanowska", 52.1792, 21.0221),
    ("nowy-swiat", "Nowy Świat", 52.2352, 21.0186),
    ("foksal", "Foksal", 52.2317, 21.0232),
    ("muzeum-narodowe", "Muzeum Narodowe", 52.2314, 21.0248),
    ("most-poniatowskiego", "Most Poniatowskiego", 52.2281, 21.0422),
    ("stadion", "Stadion Narodowy", 52.2469, 21.0448),
    ("plac-konstytucji", "Plac Konstytucji", 52.2219, 21.0158),
    ("plac-unii", "Plac Unii Lubelskiej", 52.2117, 21.0219),
    ("dworkowa", "Dworkowa", 52.2033, 21.0222),
]

ROUTES = [
    {
        "id": "demo-tram-9",
        "short_name": "9",
        "long_name": "Gocławek — P+R Aleja Krakowska",
        "mode": "tram",
        "stops": [
            "centrum",
            "politechnika",
            "pole-mokotowskie",
            "raclawicka",
            "wierzbno",
            "wil-anowska",
        ],
    },
    {
        "id": "demo-tram-7",
        "short_name": "7",
        "long_name": "P+R Aleja Krakowska — P+R Wiatraczna",
        "mode": "tram",
        "stops": [
            "nowy-swiat",
            "foksal",
            "muzeum-narodowe",
            "centrum",
            "most-poniatowskiego",
            "stadion",
        ],
    },
    {
        "id": "demo-bus-175",
        "short_name": "175",
        "long_name": "Lotnisko Chopina — Pl. Piłsudskiego",
        "mode": "bus",
        "stops": [
            "wil-anowska",
            "wierzbno",
            "raclawicka",
            "pole-mokotowskie",
            "politechnika",
            "centrum",
        ],
    },
    {
        "id": "demo-bus-118",
        "short_name": "118",
        "long_name": "Centrum — Wierzbno",
        "mode": "bus",
        "stops": ["centrum", "plac-konstytucji", "plac-unii", "dworkowa", "wierzbno"],
    },
]

# Fictional timetable: average speed in km/h and departures per direction per day.

VEHICLES = [
    ("D-101", "9", "tram", 52.2162, 21.0087, 3),
    ("D-102", "9", "tram", 52.1969, 21.0108, 5),
    ("D-201", "7", "tram", 52.2338, 21.0301, 6),
    ("D-202", "7", "tram", 52.2300, 21.0380, 9),
    ("D-301", "175", "bus", 52.2110, 21.0080, 2),
    ("D-302", "175", "bus", 52.2260, 21.0145, 8),
    ("D-401", "118", "bus", 52.2170, 21.0190, 4),
]


def seed_demo_network(session: Session) -> None:
    """Insert the fictional network unless it is already complete."""
    demo_routes = select(Route.id).where(Route.is_demo.is_(True))
    route_count = len(session.scalars(demo_routes).all())
    if route_count == len(ROUTES):
        return
    session.execute(delete(RouteStop).where(RouteStop.route_id.in_(demo_routes)))
    session.execute(delete(Route).where(Route.is_demo.is_(True)))
    session.execute(delete(Stop).where(Stop.is_demo.is_(True)))
    session.execute(delete(VehiclePosition).where(VehiclePosition.is_demo.is_(True)))
    session.flush()

    positions = {stop_id: (lat, lon) for stop_id, _, lat, lon in STOPS}
    for stop_id, name, latitude, longitude in STOPS:
        session.add(
            Stop(
                id=stop_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                location=from_shape(Point(longitude, latitude), srid=4326),
                is_demo=True,
            )
        )
    session.flush()

    route_stops: list[RouteStop] = []
    for route in ROUTES:
        coordinates = [(positions[stop_id][1], positions[stop_id][0]) for stop_id in route["stops"]]
        session.add(
            Route(
                id=route["id"],
                short_name=route["short_name"],
                long_name=route["long_name"],
                mode=route["mode"],
                shape=from_shape(LineString(coordinates), srid=4326),
                is_demo=True,
            )
        )
        for sequence, stop_id in enumerate(route["stops"]):
            route_stops.append(RouteStop(route_id=route["id"], stop_id=stop_id, sequence=sequence))

    session.flush()
    session.add_all(route_stops)

    now = datetime.now(UTC)
    for vehicle_number, line, mode, latitude, longitude, minutes_ago in VEHICLES:
        session.add(
            VehiclePosition(
                vehicle_number=vehicle_number,
                line=line,
                mode=mode,
                latitude=latitude,
                longitude=longitude,
                location=from_shape(Point(longitude, latitude), srid=4326),
                observed_at=now.replace(microsecond=0) - timedelta(minutes=minutes_ago),
                is_demo=True,
            )
        )
    session.commit()


def demo_line_delays(
    now: datetime, lines: Sequence[tuple[str, str]], points: int = 48
) -> list[DelaySnapshot]:
    """Deterministic fictional history for the demo network, clearly labelled as demo."""
    history = []
    for step in range(points):
        observed_at = (now - timedelta(minutes=2.5 * (points - 1 - step))).replace(microsecond=0)
        for index, (line, mode) in enumerate(lines):
            phase = step / 7 + index * 1.9
            base = 40 + index * 55 + 110 * math.sin(phase) + 40 * math.sin(phase * 2.3)
            vehicles = 4 + (index + step) % 3
            late = max(0, min(vehicles, round(vehicles * max(base, 0) / 400)))
            early = 1 if base < -20 else 0
            history.append(
                DelaySnapshot(
                    observed_at=observed_at,
                    line=line,
                    mode=mode,
                    vehicles=vehicles,
                    mean_delay_seconds=round(base),
                    median_delay_seconds=round(base * 0.9),
                    early=early,
                    on_time=vehicles - late - early,
                    late=late,
                )
            )
    return history
