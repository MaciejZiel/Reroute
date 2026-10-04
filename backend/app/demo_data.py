"""Small fictional network used when live sources are unavailable."""

from datetime import UTC, datetime, timedelta

from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, Point
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Route, RouteStop, Stop, VehiclePosition

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
]

VEHICLES = [
    ("D-101", "9", "tram", 52.2162, 21.0087, 3),
    ("D-102", "9", "tram", 52.1969, 21.0108, 5),
    ("D-201", "7", "tram", 52.2338, 21.0301, 6),
    ("D-202", "7", "tram", 52.2300, 21.0380, 9),
    ("D-301", "175", "bus", 52.2110, 21.0080, 2),
    ("D-302", "175", "bus", 52.2260, 21.0145, 8),
]


def seed_demo_network(session: Session) -> None:
    """Insert the fictional network once, without duplicating it on restarts."""
    if session.scalar(select(Route.id).where(Route.is_demo.is_(True)).limit(1)):
        return

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
