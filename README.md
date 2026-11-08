<p align="center">
  <img src="docs/logo.svg" width="72" height="72" alt="Reroute logo">
</p>

<h1 align="center">Reroute</h1>

<p align="center">
  <b>A live Warsaw transit map that answers one question: is my tram or bus coming, and is it late?</b>
</p>

<p align="center">
  <a href="https://reroute.maciejzielinski.dev">Live demo</a> · <a href="https://status.maciejzielinski.dev">Status</a> · <a href="https://maciejzielinski.dev">Portfolio</a>
</p>

<p align="center">
  <a href="https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="Licence: MIT" src="https://img.shields.io/badge/licence-MIT-green"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="PostGIS" src="https://img.shields.io/badge/PostGIS-336791?logo=postgresql&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-19-087EA4?logo=react&logoColor=white">
</p>

Reroute is a live map of Warsaw's trams and buses, built around one question: **is my tram or bus coming, and is it late?** Live at <https://reroute.maciejzielinski.dev>, running on a home server.

- **Stops.** Click a stop (or search by name) to see every line serving it with the next timetable departures and, where a live vehicle of that trip can be matched, its delay and expected time.
- **Lines and vehicles.** Pick a line to see its route and its moving vehicles; click a vehicle for its delay against the schedule and its next stops.
- **A collector underneath.** A separate process records every vehicle observation and every stop arrival it can derive, which is the data the model learns from.

<p align="center">
  <a href="https://reroute.maciejzielinski.dev"><img src="docs/screenshots/line.png" width="860" alt="Tram line 24 selected from Rondo Daszyńskiego: route, stops, live vehicles and departures with delays"></a>
</p>

## The machine-learning part

The feed publishes positions, not predictions, so the simplest forecast for a later stop is "the vehicle's current delay persists". Reroute also runs a small gradient-boosted tree model (LightGBM quantile regression, exported to a JSON file and evaluated by about 200 lines of pure Python, so the server needs no ML libraries) that predicts how much a delay will change between the vehicle's latest recorded stop and a stop 1 to 12 stops further along. For each upcoming stop of a selected vehicle it returns a predicted delay, an 80% range and, in the vehicle view, a one-line reason in plain words (exact TreeSHAP values, in Polish or English). Where the model must not answer (stale vehicle report, too far ahead) the baseline answers and the app says so.

<p align="center">
  <img src="docs/screenshots/vehicle-why.png" width="62%" alt="Vehicle view of tram 9: next stops with scheduled and expected times, and the line 'Why this prediction' with the likely range">
  <img src="docs/screenshots/mobile-vehicle-why.png" width="26%" alt="The same view on a 400 px wide screen">
</p>

<p align="center"><sub>The "why" line under the next stops of a vehicle: what moved the number, and the 80% range.</sub></p>

The numbers, honestly. The model was trained on **eleven service days** (26 October to 6 November 2026, 7.1 million recorded stop arrivals, including a weekend and All Saints' Day) and scored on the last day and a half, which it never saw:

| predictor (mean absolute error of the delay at the stop) | MAE |
| --- | --- |
| model: gradient-boosted trees | **59.6 s** |
| best baseline: the current delay persists, scaled on the calibration window | 67.7 s |
| ridge regression on the same features | 66.4 s |
| baseline the server falls back to: the current delay persists | 69.6 s |

- The model's error is 11.9% lower than the best baseline's (bootstrap interval 11.6% to 12.3%, resampling clusters of one line and 15 minutes), and its 80% range held 79.6% of the time, at every distance to the stop. In the rush hours it is too narrow (about 77%).
- **Does it give better advice?** A simulated passenger who is at the stop when the model's lower bound says so misses fewer vehicles and waits 8 to 11% less than one following persistence with the same margin rule, about 10 seconds per trip ([table](docs/ml/decisions.md)). Modest, and stated as such.
- **Did it hold up on days it had never seen?** The previous model, trained on three and a half weekdays, was scored unchanged on the eight days that followed, a weekend and All Saints' Day included: its error was 12.2% lower than persistence's over all eight (54.9 s against 62.5 s), and lower on every single day, between 8.6% and 15.4% ([table](docs/ml/frozen_check.md)).
- **Did more data help?** Not the error: on the same 60,000 held-out predictions the previous and the new model score 57.9 s and 57.8 s. It did tighten the range (184 s wide on average instead of 213 s) while it still holds 80% of the time, and the new model has seen a weekend and a holiday, which is why it replaced the old one. Both under-predict how much later vehicles get along a trip; more of the same days will not fix that, a new input will.

The full model card, with the data, the split, every baseline, calibration by hour and what the model does not know, is in [docs/model-card.md](docs/model-card.md). The app shows the same summary in the **About the model** panel, in English and Polish, also served at `/api/model`:

<p align="center">
  <img src="docs/screenshots/model-panel.png" width="860" alt="The About the model panel: training window, error of the model against the baseline and a simple linear model, and how often the 80% range held at each distance to the stop">
</p>

<p align="center"><sub>Vehicle screenshots are of the live deployment, taken with <code>playwright-cli</code> on 13 October 2026; the panel screenshot shows an older model's card and has not been retaken.</sub></p>

## Status and self-hosting

- **Live now:** the map, departures with delays estimated from live positions, line and vehicle views, the per-line punctuality view and the delay model, at <https://reroute.maciejzielinski.dev> (uptime on the [status page](https://status.maciejzielinski.dev)).
- **Self-hosted:** the public instance runs on a home server, not on a cloud platform.
- **Delay model:** served by default because it beat the baselines on a held-out weekday afternoon. Where it cannot answer (a trip with no recorded stop yet, a stale report) the API falls back to "the current delay persists".

## Features

- **One panel answers, the map shows where.** Search, the stop, the line and the vehicle all live in one panel on the left; the map fills the rest. On narrow screens the panel is a bottom sheet (tap or drag the handle between peek, half and full height) that opens fully when you search.
- **Start where you are.** "Stops near me" lists the nearest stations with their distance, from the browser's location and the network already on the map (nothing is sent). The last few stations you opened are one tap away (kept only in this browser).
- **Stop panel.** Search by name (diacritics-insensitive) or zoom in and click a stop; see every line at the station with its next departures, minutes first. Departures with a matched live vehicle show the delay and the expected time; late vehicles stay listed until they pass, vehicles that already passed disappear.
- **A path, not a wizard.** "Centrum › 131 › #8340" says where you are; every earlier part goes back to that view.
- **A quiet map.** At city scale every route is a thin grey line and no vehicle is drawn; colour appears only for what you pick. One "Layers" button holds the rest: trams, buses and "Show all vehicles" (the whole fleet as small dots).
- **Warsaw's colours.** Trams red, buses blue, and the yellow of the city's trams for whatever you picked; stop icons carry a tram or bus glyph, stops served by both are split. Line numbers and minutes are set in a narrow face, read at a glance like a departure board.
- **Line focus.** Pick a line: the map draws its route and stops in its colour and only its vehicles, with the line number on each, a heading wedge and the delay as the outline. Vehicles glide between the 15-second updates. When you came via a stop, a switch limits the view to "Heading to my stop" or "All of this line". Service alerts are shown.
- **Vehicle detail.** Delay against the timetable, status (waiting at the first stop, off route, stale position), the next stops with scheduled and expected times, and why the model expects what it does.
- **Chance of a delay.** Pick a stop and a line (each direction on its own): "Now, weekday 8:00–8:59: 1 in 3 trips is more than 3 min late; typically +2 min; 1 trip in 10 +6 min or more; from 40 arrivals on 18 days", and a heat map of weekdays, Saturdays and Sundays by hour shows when that line runs late. Plain counts of the last 28 days of recorded arrivals, no model: a materialized view (`stop_delay_odds`) that the collector refreshes once a night in a background thread, so a request reads a few hundred precomputed rows (`GET /api/stops/{id}/odds`).
- **Punctuality and the model (secondary).** Per-line share of early, on-time and late vehicles with a delay trend, and the model card, both one link away under the panel.
- **Light and dark, Polish and English.** The theme follows the system until the visitor picks one; the map, icons and vehicles switch with it, and both palettes are contrast-tested.
- **Keyboard and screen readers.** Picking a stop, line or vehicle moves focus to the panel's heading, section titles are real headings, the layers menu closes on Escape and returns focus, and every view passes an axe audit in both themes; touch targets are at least 44 px on phones.
- **Data pipeline for prediction.** The collector stores per-vehicle observations, derived stop arrival events and service alerts; `/api/predictions` is the contract the delay model plugs into (when its inputs are stale the current delay persists).

<p align="center">
  <img src="docs/screenshots/stop.png" width="49%" alt="Rondo Daszyńskiego station with the lines serving it and their next departures">
  <img src="docs/screenshots/mobile-line.png" width="24%" alt="Line 24 selected on a 400 px wide screen, with the bottom sheet under the map">
</p>

<p align="center"><sub>Screenshots are of the live deployment on 13 October 2026, before the current layout; they will be retaken after the next deployment.</sub></p>

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

| Layer | Used |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, GeoAlchemy2, Shapely, [gtfs-rt-kit](https://github.com/MaciejZiel/gtfs-rt-kit) |
| Database | PostgreSQL 16 with PostGIS 3.5 (daily and monthly partitions for observations and arrivals) |
| Frontend | React 19, TypeScript, Vite, MapLibre GL, OpenFreeMap vector tiles (OpenStreetMap data) with a custom dark style; oxlint and Vitest |
| Tooling | pytest, ruff, Docker Compose, GitHub Actions |

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

`docker compose up` also starts the `collector`: a separate process that polls the GTFS-Realtime vehicle and alert feeds every 20 s and records every vehicle observation, the stop arrivals derived from them and service alerts. It serves `/healthz` and Prometheus `/metrics` on port 9100 inside the container. A feed that answers but carries no vehicles, or a snapshot older than 10 minutes, records nothing: the collector logs a warning when that starts, every 30 minutes while it lasts and when it ends, and exports `feed_stale` and `polls_stale_total`. Settings (environment): `POLL_INTERVAL_S`, `OBSERVATION_RETENTION_DAYS` (7), `ARRIVAL_RETENTION_DAYS` (90; about 220 MB a day, use 60 on a 40 GB disk), `RAW_ARCHIVE_DIR` (also keep gzipped raw snapshots), `STORE_UNMATCHED`, `SCHEDULE_REFRESH_HOURS` (24; the collector re-imports the static timetable in a child process once it is older than this, because realtime trip ids stop matching an old timetable within days; 0 turns it off). Without a collector the API polls the feed itself so a plain `db + backend` start still works.

Replay archived snapshots (`<dir>/<YYYY-MM-DD>/v_<HHMMSS>.pb.gz`, UTC); repeated runs continue after the last processed file:

```bash
docker compose run --rm -v ~/reroute-data/raw:/raw collector python -m app.collector backfill --raw-dir /raw
```

Export training rows for a model: `python -m app.features export --from 2026-10-13 --to 2026-10-20 --out rows.jsonl`. The delay model is trained from the arrival table directly, see [Delay model](#delay-model); `PREDICTOR=package.module:ClassName` still plugs in any other predictor (see `backend/app/predict.py`).

Schema changes are plain SQL files in `backend/app/migrations/`, applied at start-up and by `python -m app.migrate`. The one exception is the BRIN index on `stop_arrival_events(actual_at)` that the delay model's live inputs use: it is built by `app.migrate` in code, per partition and `CONCURRENTLY`, so the collector keeps writing while it builds (checked with 4 million rows and inserts running: longest insert 25 ms). Roll it back with `python -m app.migrate --drop-arrival-time-index`.

## Deployment on a small server

Behind nginx on a self-hosted server, use the three files in `deploy/termux/`:

- `nginx-http.conf` (http block): the per-visitor `limit_req` zones, keyed on `CF-Connecting-IP`. It no longer sets `limit_req_status`, so it does not change the status code of other sites on the same nginx.
- `nginx-location.conf` (inside the server block of the host): `limit_req_status 429` for this host only, a strict zone (1 request/s, burst 5) for the endpoints that cost real work (`/api/punctuality`, `/api/stops/{id}/departures`, `/api/vehicles/...`, `/api/predictions`), a general zone for the rest of `/api/`, `Cache-Control` (`index.html` no-cache, hashed `/assets/` immutable for a year, icons for a day, nothing caches a 404) and a real 404 for `/robots.txt` and `/.well-known/` instead of the SPA page. The `location /` SPA fallback stays in your own server block.
- `nginx-headers.conf`: Content-Security-Policy, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and `Permissions-Policy`. The CSP allows exactly what the app loads (own scripts and styles, Google Fonts, OpenFreeMap tiles and glyphs, MapLibre's blob worker); it was checked against the production build in Chromium (map renders, no violations). It is included at server level and again in every location that sets its own `add_header`, because nginx drops inherited headers there. Set the absolute path of the checkout in the `include` lines.

The configuration was checked with `nginx -t` and with curl against a running nginx (headers, 404s, 429 after the burst).

The API protects itself as well: `/api/network` is serialised and gzipped once per imported timetable, with the overview's route shapes simplified to about a metre (2.4 MB gzipped became 0.6 MB for Warsaw; the line view still gets the full shape) and an ETag, so a returning visitor gets a 304; `/api/punctuality` accepts at most 360 minutes, sums windows over an hour in 5-minute steps in SQL and caches each answer for 60 s; departures predict only the rows that can still be shown; `/api/vehicles` reuses its serialised body for the same poll (at most 10 s); every request statement is cancelled after `DB_STATEMENT_TIMEOUT_MS` (5000) and waits at most `DB_POOL_TIMEOUT_S` (5) for a connection, both ending as `503` with `Retry-After`. Migrations, the collector, the timetable import and the training export use a separate engine without the statement timeout. When the real timetable is loaded but no vehicle positions arrive, `/api/vehicles` answers with no vehicles, `feed_outage: true` and a notice saying so; fictional demo vehicles appear only on a demo installation.

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
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/reroute_test uv run pytest -q   # all tests
uv run ruff check . && uv run ruff format --check .
```

Frontend:

```bash
cd frontend
npm ci
npm run lint        # oxlint
npm test            # vitest
npm run build       # type check + production build
```

To run the dev server against a backend without CORS set-up: `API_PROXY_TARGET=http://localhost:8000 VITE_API_URL= npm run dev`.

The `TEST_DATABASE_URL` database must be named `*_test` and have PostGIS available: it is wiped. Without it the pure tests still run: service-day and DST arithmetic, vehicle projection, delays and arrival events (after-midnight trips, the 25 October DST night, loop lines, gaps), a trimmed recording of the real Warsaw feed against its timetables (`backend/tests/fixtures/`), GTFS parsing, features and the predictor seam. The database tests cover the import and migrations, partitions, idempotent writes, state restore after a restart, alerts, incremental backfill, and the API (search, departures with live/late/passed/stale vehicles, 24:xx trips and DST, 503/404, predictions). CI runs them against a PostGIS service plus the frontend type check and build.

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

## Delay model

The stop panel, the vehicle view and `/api/predictions` ask a `Predictor` for the delay at each upcoming stop: the gradient-boosted model where it may answer, else "the current delay persists". Everything about it (what it is, where it answers and why it does not flicker, the data and its gaps, the time split, results against five baselines, interval calibration by distance and by hour, the comparison with the previous model, explainability, limitations and how to retrain) is in the **[model card](docs/model-card.md)**; the generated tables and charts are in [docs/ml/](docs/ml/).

Retraining runs on the maintainer's machine; the server only reads the resulting file:

```bash
REROUTE_SSH="ssh -p 8022 user@server" OBSERVATION_DAYS=30 make ml-pull
make ml-frozen      # first: does the committed model still hold on the new days? (table per day)
make ml-train       # needs numpy, scipy and lightgbm installed by hand, outside the project
make ml-test
```

## Key technical decisions

- **Demo data first, real data on demand.** A fresh `docker compose up` works offline with a fictional network; every API response carries `data_mode` (`demo` or `live`) plus a source notice. The timetable import is an explicit step and replaces only non-demo rows.
- **Delays from positions, not from a predictions feed.** The Warsaw feed publishes positions with trip ids but no delays, so the delay is the observation time minus the scheduled time interpolated at the vehicle's projection onto its trip's stops. Trip ids embed the service date (`2026-10-13:140:...`), so a vehicle maps to exactly one stored timetable. GTFS times run from "noon minus 12 hours" of the service day and can exceed 24:00; all instants are computed in UTC from that origin so after-midnight trips and DST days (23 and 25 hours) are right. Vehicles more than 250 m from the trip, waiting at the first stop or outside -15...+90 min get no delay instead of a guess.
- **Observations and arrival events are separate.** Observations (every new vehicle position, 7 days) are the raw material; arrival events (the interpolated time a vehicle passed each stop, 90 days) are what a model learns from. Arrival derivation is a pure function shared by the live loop and the backfill, so both give identical results.
- **Stations, not just platforms.** A stop panel covers all platforms with the same name within 400 m, each departure tagged with its platform.
- **A replaceable predictor.** The stop panel and vehicle view go through the `Predictor` interface, so the trained model changed numbers and added an interval and a reason, not the structure of the API.

## Limitations / next steps

- Live positions can include stale or ghost vehicles from the source feed; positions older than 3 minutes get no delay.
- Delay estimates use straight lines between stops rather than the route shape, and dwell times are not modelled. Arrival times are linear interpolations between observations (typically 30 s apart).
- The departure from the first stop is not recorded as an arrival event (the layover hides it).
- No holiday calendar feature yet; a service-date lookup from `calendar_dates.txt` would add it.
- The whole-fleet view is a deliberate opt-in: it is about 1,800 dots at city scale. "Heading to my stop" relies on the stop's departure list (next 2 hours, 10 per line), so a vehicle further away than that is not counted.
- Tests cover the pure derivation, storage, backfill and API against PostGIS, the pure-Python evaluator against committed LightGBM golden values, and, where the training libraries are installed, the model pipeline (no leakage across the time split, live inputs equal training inputs); the frontend has unit tests for the vehicle filtering, the palette contrast and the delay formatting, and Playwright browser tests with the API mocked (`npm run e2e`, in CI): a mouse click on the first search result at 1440 px and 400 px in both languages, the why line in both languages and the paused note.

## Data and attribution

- Warsaw schedules, routes and stops are provided by ZTM Warsaw in GTFS, distributed by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The feed's `attributions.txt` is retained and exposed by the API.
- Live positions are published by Miasto Stołeczne Warszawa and distributed as GTFS-Realtime by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The app displays the feed timestamp and city source link; no API key is required. The test fixtures contain a short trimmed recording of this feed and the matching ZTM timetables, with the same attribution.
- Base map: vector tiles from [OpenFreeMap](https://openfreemap.org) (free public instance, no API key, no request limits, commercial use allowed; attribution required and shown on the map), © [OpenMapTiles](https://www.openmaptiles.org/), data © OpenStreetMap contributors (ODbL). The dark style is drawn in `frontend/src/basemap.ts`. If you self-host the tiles, point `url` and `glyphs` there at your own instance.

Source code, comments and technical documentation are in English.

## Licence

MIT License, see [LICENSE](LICENSE). Transit data fetched from the GTFS feeds remains subject to the terms of its source.
