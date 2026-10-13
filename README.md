# Reroute

**A live Warsaw transit map that answers one question: is my tram or bus coming, and is it late?** Click a stop to see every line serving it with the next timetable departures and, where a live vehicle of that trip can be matched, its delay; click a line to see only its route and moving vehicles; click a vehicle for its delay against the schedule and its next stops. Underneath, a collector records every vehicle observation and stop arrival so a delay-prediction model can be trained later.

[![CI](https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![FastAPI + PostGIS](https://img.shields.io/badge/FastAPI-PostGIS-009688.svg)
![React + MapLibre](https://img.shields.io/badge/React-MapLibre-3178c6.svg)

![Plac Zbawiciela: lines serving the stop with next departures and live delays](docs/screenshots/stop-departures.png)

## What it does

- **Stop panel.** Click a stop on the map (or search by name, diacritics-insensitive) and see all lines at the station, each with its next departures from the ZTM GTFS timetable. Departures with a matched live vehicle show the delay and the expected time; late vehicles stay listed until they pass, vehicles that already passed disappear.
- **Line focus.** Click a line: the map highlights its route and stops, hides all other vehicles and refreshes that line's vehicles every 15 seconds. Service alerts for the line are shown.
- **Vehicle detail.** Click a vehicle: delay against the timetable, status (waiting at the first stop, off route, stale position) and the next stops with scheduled and expected times.
- **Delay colours everywhere.** Vehicle outlines are green (on time), orange (more than 3 min late), blue (more than 1 min early) or grey (unknown).
- **Punctuality (secondary).** Per-line share of early, on-time and late vehicles and a delay trend, from the per-minute rollup the collector writes.
- **Bilingual, responsive UI** (Polish and English; on a phone the panel sits under the map).
- **Data pipeline for prediction.** The collector stores per-vehicle observations, derived stop arrival events and service alerts; `app/features.py` exports leak-free training rows and `/api/predictions` is the contract a trained model plugs into (today: the current delay persists).

| Line focus | Vehicle detail |
| --- | --- |
| ![Only line 4 is shown: route, stops and live vehicles](docs/screenshots/line-live.png) | ![Vehicle 3622 is 5 minutes late, with next stops and expected times](docs/screenshots/vehicle-detail.png) |

| Punctuality (secondary view) | Mobile |
| --- | --- |
| ![Share of early, on-time and late vehicles per line and a delay trend](docs/screenshots/punctuality.png) | ![Stop panel under the map on a phone](docs/screenshots/mobile.png) |

Screenshots are from the live Warsaw data on 13 October 2026.

## Architecture

```mermaid
flowchart LR
    GTFS[(ZTM GTFS archive<br/>mkuran.pl)] -->|python -m app.import_gtfs| DB
    RT[(GTFS-Realtime<br/>vehicles.pb, alerts.pb)] -->|gtfs-rt-kit, every 20 s| COL[collector<br/>python -m app.collector]
    RAW[(raw .pb.gz snapshots)] -->|backfill| COL
    COL -->|observations, arrival events,<br/>vehicle_latest, alerts| DB[(PostgreSQL + PostGIS)]
    DB --> API[FastAPI<br/>/api/stops /api/lines<br/>/api/vehicles /api/predictions<br/>/api/punctuality]
    API --> WEB[React + MapLibre GL]
```

See [docs/design.md](docs/design.md) for the data model, retention, the collector and the prediction seam.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, GeoAlchemy2, Shapely, [gtfs-rt-kit](https://github.com/MaciejZiel/gtfs-rt-kit)
- **Database:** PostgreSQL 16 with PostGIS 3.5 (daily and monthly partitions for observations and arrivals)
- **Frontend:** React 19, TypeScript, Vite, MapLibre GL, OpenStreetMap tiles
- **Tooling:** pytest, ruff, Docker Compose, GitHub Actions

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs

A small fictional demo network loads on first start so the app works with no import. To import the current Warsaw bus and tram timetable (about 100 MB, cached under `data/`; 3-4 minutes):

```bash
docker compose exec backend python -m app.import_gtfs
```

Databases imported by an older version need this import again (the API answers 503 with a hint until then). Repeat imports reuse the cached archive; pass `--force-download` for the latest version or `--file` for a local archive. Re-importing replaces only the real network and keeps the demo network.

## Collector

`docker compose up` also starts the `collector`: a separate process that polls the GTFS-Realtime vehicle and alert feeds every 20 s and records every vehicle observation, the stop arrivals derived from them and service alerts. It serves `/healthz` and Prometheus `/metrics` on port 9100 inside the container. Settings (environment): `POLL_INTERVAL_S`, `OBSERVATION_RETENTION_DAYS` (7), `ARRIVAL_RETENTION_DAYS` (90; about 220 MB a day, use 60 on a 40 GB disk), `RAW_ARCHIVE_DIR` (also keep gzipped raw snapshots), `STORE_UNMATCHED`. Without a collector the API polls the feed itself so a plain `db + backend` start still works.

Replay archived snapshots (`<dir>/<YYYY-MM-DD>/v_<HHMMSS>.pb.gz`, UTC); repeated runs continue after the last processed file:

```bash
docker compose run --rm -v ~/reroute-data/raw:/raw collector python -m app.collector backfill --raw-dir /raw
```

Export training rows for a model: `python -m app.features export --from 2026-10-13 --to 2026-10-20 --out rows.jsonl`. To use a trained model set `PREDICTOR=package.module:ClassName` (see `backend/app/predict.py`).

Schema changes are plain SQL files in `backend/app/migrations/`, applied at start-up and by `python -m app.migrate`.

## Deployment on a small server

`docker-compose.prod.yml` runs nginx (serving the built frontend and proxying `/api`), the API, the collector and Postgres; only port 80 is published.

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" > .env
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api python -m app.import_gtfs
```

Plan for 2 vCPU, 4 GB RAM and 40 GB of disk (arrival retention defaults to 60 days there).

## Tests

```bash
cd backend
uv sync --extra dev        # or: pip install -e '.[dev]'
uv run pytest -q           # database tests are skipped unless TEST_DATABASE_URL is set
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/reroute_test uv run pytest -q   # all 58
uv run ruff check . && uv run ruff format --check .
```

The `TEST_DATABASE_URL` database must be named `*_test` and have PostGIS available: it is wiped. Without it the pure tests still run: service-day and DST arithmetic, vehicle projection, delays and arrival events (after-midnight trips, the 25 October DST night, loop lines, gaps), a trimmed recording of the real Warsaw feed against its timetables (`backend/tests/fixtures/`), GTFS parsing, features and the predictor seam. The database tests cover the import and migrations, partitions, idempotent writes, state restore after a restart, alerts, incremental backfill, and the API (search, departures with live/late/passed/stale vehicles, 24:xx trips and DST, 503/404, predictions). CI runs them against a PostGIS service plus the frontend type check and build.

## Performance

Measured on the full Warsaw network (318 bus and tram lines, 7,253 stops, 20,164 timed segments; feed of 9 Oct 2026) with the stack running in Docker Compose on a 16-thread laptop, using `backend/scripts/benchmark_api.py --runs 60 --seed 7` (random stops and lines from the network, closures and slowdowns alternating, 30 min):

| Request | Before (`main` at #3) p50 / p95 | After p50 / p95 |
| --- | --- | --- |
| `POST /api/simulations`, stop | 397 / 487 ms | 30 / 84 ms |
| `POST /api/simulations`, line | 465 / 846 ms | 105 / 483 ms |
| `POST /api/simulations`, all | 416 / 756 ms | 59 / 442 ms |
| `GET /api/network` | 731 ms, 10.5 MB | 65 ms, 2.4 MB gzip |

What changed:

- **Graph cache.** The stop graph (stops, segments, ~55k walking links from a lat/lon grid) took ~0.35 s to load and build on every request. It is now built once per data mode and timetable version (`feed_metadata.downloaded_at`), so a re-import invalidates it; the first query after start-up still pays ~0.5 s.
- **Search reuse and bounds.** Undisrupted searches are cached per origin and target set (the graph is immutable), edges are pre-grouped per stop and line, and a disrupted search stops one hour past the usual journey time instead of sweeping the city for unreachable stops.
- **Network response.** The 10.5 MB GeoJSON is serialised and gzipped once per timetable instead of on every page load.
- **Index.** `route_stops(stop_id)` for the "lines at these stops" lookups (the primary key starts with `route_id`).

The remaining cost is pure-Python Dijkstra: closing a long line samples up to eight journeys of 30–60 min across the city, and each search settles several thousand states.

## Key technical decisions

- **Demo data first, real data on demand.** A fresh `docker compose up` works offline with a fictional network, and every API response carries `data_mode` (`demo` or `live`) plus a source notice, so the UI can always say what it is showing. The 100 MB GTFS import is an explicit step, and it replaces only non-demo rows.
- **Live feed fetched lazily, with a short shared cache.** Vehicle positions are not stored. The first request after the 12-second cache expires fetches the protobuf feed, behind a lock so concurrent requests don't hit the source twice. This keeps the server stateless and respectful of a free public feed, at the cost of one slower request per refresh.
- **Shortest paths on a route-expanded stop graph.** The importer keeps one *segment* per pair of consecutive stops of a route, with the mean scheduled run time and the number of trips on the busiest service day of the feed. A search state is "at stop S" or "on line L at stop S", so boarding can carry an expected wait (half the headway, 1–15 min) plus a 2-minute transfer penalty, and walking links stops within 400 m (×1.3 street detour at 1.2 m/s). A closure deletes the affected segments, a slowdown multiplies their run times, and plain Dijkstra with an early exit once all targets are settled is fast enough (a few ms per origin on the 7k-stop network). Journeys are sampled per affected line and direction: two stops either side of a closed stop, or five evenly spaced stops along a closed line. There is no demand data, so "added minutes" are per journey, not passenger-weighted.
- **Delays from positions, not from a predictions feed.** The Warsaw feed publishes positions with trip ids but no delays, so the delay is the feed timestamp minus the scheduled time interpolated at the vehicle's projection onto the trip's stop-to-stop lines. The service day is whichever of today/yesterday gives the smaller delay (GTFS times can exceed 24:00). Vehicles more than 250 m from their trip, standing at the first stop (layover) or outside −15…+90 min are skipped. Trip timetables are stored compactly: ~1,900 shared stop patterns plus one comma-separated departures string per trip (~80 MB in PostgreSQL for ~270k trips). Only per-line aggregates are kept, for 7 days.
- **Cheap geo filtering for nearby stops.** Candidates come from a latitude/longitude bounding box in SQL, then exact haversine distances are computed in Python. With ~7k stops this is fast and easy to unit test. PostGIS geometries are stored for route shapes and stop locations, so the query can move to `ST_DWithin` with a spatial index when needed.

## Limitations / next steps

- Live positions can include stale or ghost vehicles from the source feed; vehicles older than 2 minutes are flagged as stale but still shown.
- Delay estimates use straight lines between stops rather than the route shape, so a vehicle on a winding section can be placed slightly off; stop dwell times are not modelled. Snapshots are only taken while someone has the map open or `collect_delays` runs, and the demo network shows a clearly labelled fictional punctuality history.
- The schema is created with `create_all` at startup, with an in-place column type fix; Alembic migrations would replace this.
- Results are timetable estimates, not an official forecast or passenger count: there is no demand data, departures are averaged into a headway (no exact connection times), and a closed line is assumed to have no replacement service. The legacy impact score (affected stops × duration) is still returned for compatibility.
- The graph and network caches live in the API process, which suits the single-worker Compose setup; several workers would each build their own copy (~0.5 s on first use). Line closures on long routes still take a few hundred ms in pure-Python Dijkstra; A* or contraction hierarchies would be the next step.
- Tests cover GTFS parsing, routing and delay estimation without a database; there are no API-level tests against PostGIS or frontend tests yet.

## Performance

Measured on the real Warsaw timetable (314 lines, 7,241 stops, 282,430 trips over 9 service days) with about 1,900 live vehicles, local PostGIS in Docker, with `backend/scripts/benchmark_live.py --runs 100 --seed 7`:

| Request | p50 | p95 |
| --- | --- | --- |
| stop departures, random stops (whole station) | 29 ms | 53 ms |
| stop departures, 20 busiest interchanges | 62 ms | 108 ms |
| stop search | 15 ms | 20 ms |
| line detail (shape and stops) | 13 ms | 22 ms |
| line vehicles | 12 ms | 19 ms |
| all vehicles | 70 ms | 139 ms |
| vehicle detail | 14 ms | 21 ms |

Departure lookups use `pattern_stops(stop_id)`, `trip_schedules(pattern_id, service_date)` and the trip primary key, and read the departure time with `split_part` in SQL. Collector: ~86 MiB RSS, 0.14 s per poll; observations ~146 B/row (~6.5M rows, ~0.95 GB a day measured live), arrival events ~170 B/row (~220 MB a day).

## Key technical decisions

- **Demo data first, real data on demand.** A fresh `docker compose up` works offline with a fictional network; every API response carries `data_mode` (`demo` or `live`) plus a source notice. The timetable import is an explicit step and replaces only non-demo rows.
- **Delays from positions, not from a predictions feed.** The Warsaw feed publishes positions with trip ids but no delays, so the delay is the observation time minus the scheduled time interpolated at the vehicle's projection onto its trip's stops. Trip ids embed the service date (`2026-10-13:140:...`), so a vehicle maps to exactly one stored timetable. GTFS times run from "noon minus 12 hours" of the service day and can exceed 24:00; all instants are computed in UTC from that origin so after-midnight trips and DST days (23 and 25 hours) are right. Vehicles more than 250 m from the trip, waiting at the first stop or outside -15...+90 min get no delay instead of a guess.
- **Observations and arrival events are separate.** Observations (every new vehicle position, 7 days) are the raw material; arrival events (the interpolated time a vehicle passed each stop, 90 days) are what a model learns from. Arrival derivation is a pure function shared by the live loop and the backfill, so both give identical results.
- **Stations, not just platforms.** A stop panel covers all platforms with the same name within 400 m, each departure tagged with its platform.
- **A replaceable predictor.** The stop panel and vehicle view already go through the `Predictor` interface, so a trained model changes numbers, not the UI or the API.

## Limitations / next steps

- Live positions can include stale or ghost vehicles from the source feed; positions older than 3 minutes get no delay.
- Delay estimates use straight lines between stops rather than the route shape, and dwell times are not modelled. Arrival times are linear interpolations between observations (typically 30 s apart).
- The departure from the first stop is not recorded as an arrival event (the layover hides it).
- No holiday calendar feature yet; a service-date lookup from `calendar_dates.txt` would add it.
- The map shows the whole fleet at once, which is dense at city zoom; clustering vehicles is a possible next step.
- Tests cover the pure derivation, storage, backfill and API against PostGIS; there are no frontend tests yet.

## Data and attribution

- Warsaw schedules, routes and stops are provided by ZTM Warsaw in GTFS, distributed by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The feed's `attributions.txt` is retained and exposed by the API.
- Live positions are published by Miasto Stołeczne Warszawa and distributed as GTFS-Realtime by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The app displays the feed timestamp and city source link; no API key is required. The test fixtures contain a short trimmed recording of this feed and the matching ZTM timetables, with the same attribution.
- Base map © OpenStreetMap contributors. Map tiles are loaded from `tile.openstreetmap.org`; attribution and caching requirements apply: <https://operations.osmfoundation.org/policies/tiles/>.

Source code, comments and technical documentation are in English.

## License

MIT License, see [LICENSE](LICENSE). Transit data fetched from the GTFS feeds remains subject to the terms of its source.
