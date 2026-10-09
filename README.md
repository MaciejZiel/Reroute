# Reroute

Reroute is a local-first Warsaw transit control room. Explore public transport routes and live vehicle positions, then simulate how a disruption could affect lines and stops.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Open the application at <http://localhost:5173> and the API documentation at <http://localhost:8000/docs>. The demo network works immediately and needs no credentials. Live vehicle positions are fetched automatically from the public Warsaw GTFS-Realtime feed.

To import the current Warsaw bus and tram timetable and replace the fictional network, run:

```bash
docker compose exec backend python -m app.import_gtfs
```

The archive is about 100 MB and is cached under `data/`; repeat imports use that local copy. Pass `--force-download` to fetch the latest version. The interface switches to fictional demo vehicles when live positions are unavailable; an imported GTFS network remains available.

## Data and attribution

- Warsaw schedules, routes and stops are provided by ZTM Warsaw in GTFS, distributed by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The feed's `attributions.txt` is retained and exposed by the API.
- Live positions are published by Miasto Stołeczne Warszawa and distributed as GTFS-Realtime by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The app displays the feed timestamp and city source link; no API key is required.
- Base map © OpenStreetMap contributors. Map tiles are loaded from `tile.openstreetmap.org`; attribution and caching requirements apply: <https://operations.osmfoundation.org/policies/tiles/>.

Live positions may include stale or ghost vehicles from the source feed. Disruption impact is an illustrative route-stop and duration score, not an official service forecast or passenger count.

## Development

Source code, comments and technical documentation are in English. The interface supports Polish and English.

## License

MIT License, see [LICENSE](LICENSE). Transit data fetched from the GTFS feeds remains subject to the terms of its source.
