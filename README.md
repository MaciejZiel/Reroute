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

The feed publishes positions, not predictions, so the simplest forecast for a later stop is "the vehicle's current delay stays". Reroute also runs a small gradient-boosted tree model (LightGBM quantile regression, exported to a JSON file and evaluated by about 200 lines of pure Python, so the server needs no ML libraries) that predicts how much a delay will change between the vehicle's latest recorded stop and a stop 1 to 12 stops further along. For each upcoming stop of a selected vehicle it returns a predicted delay, an 80% range and, in the vehicle view, a one-line reason in plain words (exact TreeSHAP values, in Polish or English). Where the model must not answer (stale vehicle report, too far ahead) the baseline answers and the app says so.

<p align="center">
  <img src="docs/screenshots/vehicle-why.png" width="62%" alt="Vehicle view of tram 9: next stops with scheduled and expected times, and the line 'Why this prediction' with the likely range">
  <img src="docs/screenshots/mobile-vehicle-why.png" width="26%" alt="The same view on a 400 px wide screen">
</p>

<p align="center"><sub>The "why" line under the next stops of a vehicle: what moved the number, and the 80% range.</sub></p>

The numbers, honestly. Everything below comes from **three and a half weekdays** (Monday 26 to Thursday 29 October 2026, 2,379,956 recorded stop arrivals), split by time: the model is scored on Thursday 12:00 to 17:20 Warsaw time, which it never saw.

| predictor (mean absolute error of the delay at the stop) | MAE |
| --- | --- |
| model: gradient-boosted trees | **74.4 s** |
| best baseline: the current delay persists, scaled on the calibration window | 83.8 s |
| ridge regression on the same features | 79.7 s |
| baseline the server falls back to: the current delay persists | 93.6 s |

- The model's error is 11.2% lower than the best baseline's (bootstrap interval 10.7% to 11.7%, resampling clusters of one line and 15 minutes), largest for the nearest stops (18% for stops under 2 minutes of scheduled time away).
- On the same Thursday afternoon this model's error was 3.4% lower than the previous model's, which was trained on one evening (interval 3.0% to 3.7%), and the previous model's 80% range held only 75.3% of the time against 80.2%.
- The 80% range contained 79.8% of the arrivals in the test window. It is too narrow in the afternoon rush (76.8% at 16:00, where vehicles end up later than the range more often); see the model card.
- Weekdays only: no weekend, holiday or bad-weather day is in the data yet.

The app shows the same summary in the **About the model** panel, in English and Polish, also served at `/api/model`:

<p align="center">
  <img src="docs/screenshots/model-panel.png" width="860" alt="The About the model panel: training window, error of the model against the baseline and a simple linear model, and how often the 80% range held at each distance to the stop">
</p>

<p align="center"><sub>Vehicle screenshots are of the live deployment, taken with <code>playwright-cli</code> on 13 October 2026; the panel screenshot shows the previous model's card (one evening of data) and has not been retaken for the current one.</sub></p>

The full model card, with what it knows and does not know, is [below](#delay-model). Weekend, morning-rush, holiday and winter behaviour are untested.

## Status and self-hosting

- **Live now:** the map, departures with delays estimated from live positions, line and vehicle views, the per-line punctuality view and the delay model, at <https://reroute.maciejzielinski.dev> (uptime on the [status page](https://status.maciejzielinski.dev)).
- **Self-hosted:** the public instance runs on a home server, not on a cloud platform.
- **Delay model:** served by default because it beat the baselines on held-out data from its single evening. Where it cannot answer (a trip with no recorded stop yet, a stale report) the API falls back to "the current delay persists".

## Features

- **Three steps: stop, line, vehicle.** A progress rail at the top of the panel shows where you are; finished steps are clickable to go back. On narrow screens the panel is a bottom sheet (tap or drag the handle between peek, half and full height).
- **Quiet by default.** No vehicles are drawn until you pick a line. A single switch, "Show all vehicles" (off by default), draws the whole fleet as small unlabelled dots for those who want the city-wide picture; names appear only when zoomed in.
- **Stop panel.** Search by name (diacritics-insensitive) or zoom in and click a stop; see all lines at the station with their next timetable departures. Departures with a matched live vehicle show the delay and the expected time; late vehicles stay listed until they pass, vehicles that already passed disappear.
- **Mode-aware stop icons.** Bus stops are mint, tram stops coral and stops served by both are split; a small glyph says which. Stops are hidden at city zoom (the route lines carry the overview), thin out by collision as you zoom in, get names from zoom 16 and the selected station is highlighted in gold with a single label.
- **Line focus.** Pick a line: the map highlights its route and stops and shows only that line's vehicles, with the line number on each, a heading wedge and the delay colour as the outline. Vehicles glide between the 15-second updates. When you came via a stop, a switch limits the view to "Heading to my stop" (the live vehicles that still have a departure at that stop, so direction is handled by the timetable) or "All of this line". Service alerts are shown.
- **Vehicle detail.** Click a vehicle: delay against the timetable, status (waiting at the first stop, off route, stale position) and the next stops with scheduled and expected times.
- **Delay colours.** Vehicle outlines are green (on time), orange (more than 3 min late), blue (more than 1 min early) or grey (unknown).
- **Punctuality (secondary).** Per-line share of early, on-time and late vehicles and a delay trend, from the per-minute rollup the collector writes (windows of 30 minutes to 6 hours).
- **Bilingual, responsive UI** (Polish and English; on narrow screens the panel sits under the map).
- **Data pipeline for prediction.** The collector stores per-vehicle observations, derived stop arrival events and service alerts; `app/features.py` exports leak-free training rows and `/api/predictions` is the contract a trained model plugs into (the LightGBM delay model is the default; when its inputs are stale the current delay persists).

<p align="center">
  <img src="docs/screenshots/stop.png" width="49%" alt="Rondo Daszyńskiego station with the lines serving it and their next departures">
  <img src="docs/screenshots/mobile-line.png" width="24%" alt="Line 24 selected on a 400 px wide screen, with the bottom sheet under the map">
</p>

<p align="center"><sub>Screenshots are of the live deployment, taken with <code>playwright-cli</code> on 13 October 2026 at 1440 px and 400 px widths.</sub></p>

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

`docker compose up` also starts the `collector`: a separate process that polls the GTFS-Realtime vehicle and alert feeds every 20 s and records every vehicle observation, the stop arrivals derived from them and service alerts. It serves `/healthz` and Prometheus `/metrics` on port 9100 inside the container. Settings (environment): `POLL_INTERVAL_S`, `OBSERVATION_RETENTION_DAYS` (7), `ARRIVAL_RETENTION_DAYS` (90; about 220 MB a day, use 60 on a 40 GB disk), `RAW_ARCHIVE_DIR` (also keep gzipped raw snapshots), `STORE_UNMATCHED`, `SCHEDULE_REFRESH_HOURS` (24; the collector re-imports the static timetable in a child process once it is older than this, because realtime trip ids stop matching an old timetable within days; 0 turns it off). Without a collector the API polls the feed itself so a plain `db + backend` start still works.

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

The API protects itself as well: `/api/punctuality` accepts at most 360 minutes, sums windows over an hour in 5-minute steps in SQL and caches each answer for 60 s; departures predict only the rows that can still be shown; `/api/vehicles` reuses its serialised body for the same poll (at most 10 s); every request statement is cancelled after `DB_STATEMENT_TIMEOUT_MS` (5000) and waits at most `DB_POOL_TIMEOUT_S` (5) for a connection, both ending as `503` with `Retry-After`. Migrations, the collector, the timetable import and the training export use a separate engine without the statement timeout. When the real timetable is loaded but no vehicle positions arrive, `/api/vehicles` answers with no vehicles, `feed_outage: true` and a notice saying so; fictional demo vehicles appear only on a demo installation.

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

`/api/predictions`, the stop panel and the vehicle view ask a `Predictor` for the delay at each upcoming stop. Two exist: the baseline (the vehicle's current delay persists) and a gradient-boosted tree model that predicts how much the delay *changes* between the vehicle's latest recorded stop and a later stop of the same trip, together with an 80% interval and the factors behind the number. Example answer for a stop three stops ahead of a vehicle that is 3 min 15 s late (a real held-out case; it actually arrived 3 min 35 s late):

```json
{"predicted_delay_s": 199, "lower_delay_s": 162, "upper_delay_s": 245,
 "why": {"summary": "Now 3 min 15 s late; expected 3 min 19 s late at this stop. Main factors: vehicles at this stop were 34 s late on average in the last 30 minutes (-16 s); the stop is 3 stops ahead, 3 min of running time (+11 s).",
         "factors": [{"feature": "stop_recent_delay_s", "effect_seconds": -16, "text": "Vehicles at this stop were 34 s late on average in the last 30 minutes"}, "..."]}}
```

The vehicle view shows the same reason as one quiet line under the next stops, naming the stop it explains, with the likely range to the second. Reasons are written in the UI language (`?lang=pl` or `en` on `/api/vehicles/...` and `/api/predictions`; Polish plurals, decimal comma, words for every factor). When the model is paused the same place shows a calm note instead ("Live prediction paused: waiting for a fresh vehicle report"), and `model_status` / `pause_reason` say why in the API (`model`, `held`, `paused`, `out_of_range`, and `passed` when the baseline answered because the vehicle has already reached or passed that stop; the vehicle view shows a short note for that too). Reasons are exact TreeSHAP values of the median model, computed for the first prediction of a request only (the exact computation is the slow part on a small server). Predictions the baseline answers carry no reason.

### Model card

**What it is.** LightGBM quantile regression (median, 10% and 90% quantiles of the delay change; 400 trees each, 15 leaves), exported to a JSON file of flat tree arrays and evaluated by about 200 lines of pure Python (`backend/app/ml/ensemble.py`), so the server needs no extra dependency (no scikit-learn, no LightGBM, no NumPy; ONNX was not needed). The interval is widened by split-conformal calibration on a separate later fold (a shift of about 1.5 s: the raw quantile heads were already close to calibrated). Version `20261029-8e1d57de`, file `backend/models/delay_model.json` (about 1.4 MB).

The tree count is capped (`--max-rounds 400`): early stopping had not stopped at the default 800, but the server walks every tree for each prediction and for its reasons, and on the server 800 trees per head doubled the time of an explained prediction (675 ms against 307 ms for the previous model). At 400 trees it takes 282 ms and a plain prediction 2.9 ms; the test MAE is 74.4 s against 74.0 s with 800 trees.

**Where the model answers, and why it does not flicker.** Only where it was trained and evaluated: the vehicle's newest report is at most 120 s old, its last recorded stop is at most 10 minutes old, and the stop asked about is at most 12 stops ahead (and within the scheduled running time seen in training). The two age limits were set from the recorded feed, not guessed. Vehicles report a new position every 22 s at the median, but 10% of the gaps between reports are longer than 45 s, so at a random moment the newest report is at most 45 s old 89.5% of the time, 60 s 92.7%, 90 s 95.8% and 120 s 97.5%; the newest recorded stop is at most 300 s old 98.3% of the time and 600 s 99.7% (one evening, so these shares are indicative). A first version used 45 s and made a vehicle's answer alternate between the model and the baseline from poll to poll. Now training simulates requests at random moments (so the report age is what a request really sees) and the report's age is a feature, so the model is evaluated on the same ages it is used on. On top of that the last model answer for a stop is held for 60 s when a report is late: the information behind a held answer is never older than 120 s + 60 s = 180 s, the age after which the app itself stops trusting a vehicle's delay. After that, or without a live vehicle, or beyond the trained stops, the baseline answers with no interval and no reason, the departure is not marked live because of the model, and the vehicle view says why the model is paused. This is enforced in `app/ml/live.py` and `ModelPredictor` and covered by tests.

**Data.** Four service dates, Monday 2026-10-26 03:16 to Thursday 2026-10-29 17:20 Warsaw time (86 hours) of the live Warsaw feed: 2,379,956 recorded stop arrivals on 95,489 trips, 312 lines and 6,696 stops. The source feed carried no vehicles on Wednesday 28 October from 04:00 to 15:00, so that morning is missing. Weekdays of one season only: no weekend, no holiday, no bad-weather day, no autumn-versus-summer difference. Weather was not used (no source with a clear free licence was wired in). Arrival times are themselves interpolated between 20 to 30 s observations. Days before 26 October cannot be added: from 22 to 26 October the stored timetable no longer matched the feed (fixed by an automatic daily re-import), and the vehicle reports of 13 to 21 October, which the model needs as an input, had already been deleted after 7 days (the server now keeps them 30 days).

**Samples and split.** A request can arrive at any moment, so predictions are simulated at moments spread over each trip's life (one per four minutes on average, `--moment-every 240`, at uniformly random times, so the age of the newest vehicle report is what a live request sees; neighbouring minutes of a trip are nearly the same sample and one per minute did not change the result: 74.0 s against 73.6 s at 800 trees): the origin is the newest arrival known then, the target is a stop 1, 2, 3, 4, 6, 8 or 12 stops further along the same trip that has not been reached yet (4,715,338 samples). The split is by time only, into four consecutive windows, and a sample belongs to a window only if the prediction moment and the arrival it predicts both lie inside it (14,268 samples straddling a boundary are dropped). The boundaries were set by hand before training (`--fractions 0.6939 0.8798 0.9379`) so that the interval is calibrated on a morning and tested on an afternoon: with the default fractions the calibration window would have been Wednesday 20:41 to Thursday 03:34, evening and night only. Train Monday 03:16 to Wednesday 15:00 (2,868,487 samples), early stopping to Thursday 07:00 (873,887), interval calibration and best-baseline choice Thursday 07:00 to 12:00 (451,454), test Thursday 12:00 to 17:20 (507,242). Features use only arrivals known at the prediction moment (unit-tested by changing everything that happens later), and the live code computes the inputs with the same functions as training (tested for equality against the database at arbitrary moments, with a vehicle delay).

**Features.** Stops ahead and scheduled running time to the target, and how long ago the last stop was recorded; the vehicle's own latest reported delay, how old that report is, and the delay at its last recorded stop and how it changed; the delay trend over 10 min; the mean delay of other vehicles on the line (15 min) and at the target stop (30 min); the delay of the vehicle ahead on the same line at the target stop and its scheduled headway; tram or bus. Hour, weekday and stop number are implemented but switched off (`--calendar-features`): with three and a half weekdays they would mostly memorise the clock.

**Results on the held-out later window** (MAE and RMSE in seconds; the intervals come from a bootstrap that resamples clusters of one line and one 15-minute block; "MAE under 2 min" etc. bin by the scheduled time from the last stop to the target):

| predictor | MAE s (bootstrap 95% interval) | RMSE s (bootstrap 95% interval) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 306.7 (297.0 to 316.9) | 460.4 (446.7 to 474.9) | -295.5 | 284.2 | 287.6 | 302.9 | 336.4 |
| persistence (BaselinePredictor) | 93.6 (91.1 to 96.1) | 165.4 (160.5 to 171.0) | -70.6 | 31.7 | 52.6 | 89.0 | 155.6 |
| historical median (line, stop, hour) | 202.9 (196.0 to 210.4) | 348.5 (331.0 to 365.7) | -14.2 | 198.2 | 196.1 | 201.4 | 213.1 |
| calibrated persistence | 83.8 (81.9 to 85.8) | 145.7 (141.3 to 150.6) | -25.7 | 29.3 | 49.3 | 81.6 | 134.4 |
| gradient boosted trees (median) | 74.4 (72.7 to 76.2) | 129.1 (125.0 to 133.4) | -35.0 | 23.9 | 44.4 | 74.0 | 117.3 |
| ridge regression (same features) | 79.7 (78.0 to 81.5) | 133.5 (129.5 to 137.8) | -23.0 | 37.1 | 50.4 | 76.6 | 123.6 |

80% interval on the test fold: model coverage 79.8% (79.4% to 80.1%), mean width 213 s; persistence plus empirical residual quantiles: coverage 76.2% (75.6% to 76.8%), mean width 206 s.

Delays on these afternoons are large: vehicles were on average about five minutes late (the bias of the zero-delay row), and they kept getting later along the trip, which is why every predictor underestimates (negative bias) and why "the delay persists" is much weaker than on the one evening the previous model was trained on.

The ridge row is a plain linear regression on the same 14 features and the same target, fitted offline on the same training window and scored by the same code (`backend/ml/linear.py`); it is not served. It beats both persistence baselines, and the trees' MAE is 6.7% lower than ridge (bootstrap interval 6.3% to 7.0%). That comparison is not like-for-like: ridge minimises squared error and its penalty (10,000) is the largest value on the grid searched, whereas the trees minimise a median loss and are scored by MAE. Treat it as a rough linear reference, not as proof that a linear model cannot match the trees. Ridge is weak for the nearest stops, where persistence is hard to beat.

The best baseline, chosen on the calibration window and not on the test window, is persistence scaled by a factor fitted there ("calibrated persistence"). The model's MAE is **11.2% lower** (bootstrap interval 10.7% to 11.7%), RMSE about 11% lower. By horizon the relative gain is 18%, 10%, 9% and 13%. The rule for serving the model by default (at least 5% lower MAE than the best baseline with the bootstrap interval above zero) is in the code; it passed, so the model is the default (`promoted: true` in the card inside the model file).

How far to trust these numbers: the bootstrap interval is sampling uncertainty inside one Thursday afternoon. Clusters of one line and 15 minutes still ignore shocks that hit the whole network at once, and none of it says anything about weekends. The fold boundaries were chosen by hand for this run (to keep the night out of the calibration window) before the model was fitted, not tuned on the test result; the tree cap was chosen after comparing 800 and 400 trees on the test window, so that choice has seen it (the difference was 0.4 s). The historical-median row is weak because three and a half days give few observations per line, stop and hour.

**Against the previous model.** The previous model (`20261013-f2a7a97f`, one evening of data) and this one were run, unchanged, through the server's evaluator on the same 40,000 predictions drawn at random from the Thursday 12:00 to 17:20 test window (one prediction per four minutes of each trip, 8,954 trips): MAE 76.5 s against 73.9 s, so 3.4% lower for the new model (bootstrap interval 3.0% to 3.7%, line-and-15-minute clusters), and the 80% range held 75.3% of the time against 80.2%. The previous model's own frozen check, from 13 October, is kept in [docs/ml/frozen_check.md](docs/ml/frozen_check.md). `make ml-frozen` scores a stored model on later data; repeat it whenever the collector has gathered new days, before anyone retrains.

<p align="center">
  <img src="docs/ml/mae_by_horizon.svg" width="720" alt="MAE by horizon, model against persistence">
</p>

**Did the 80% range hold?** Checked on the held-out window, which follows the calibration window that set the range's width (so not on the data that set it), per scheduled time to the stop and per hour of the Warsaw clock. A bin with fewer than 200 predictions or 30 trips is not shown; every share has a cluster-bootstrap interval.

| scheduled time to the stop | predictions | coverage (95% interval) | missed below / above | mean width |
| --- | --- | --- | --- | --- |
| under 2 min | 27,865 | 82.9% (82.4% to 83.4%) | 6.8% / 10.3% | 75 s |
| 2 to 5 min | 160,160 | 80.6% (80.3% to 80.9%) | 7.7% / 11.7% | 131 s |
| 5 to 10 min | 172,896 | 79.5% (79.1% to 79.9%) | 7.5% / 13.1% | 212 s |
| 10 min or more | 146,321 | 78.7% (78.1% to 79.2%) | 6.7% / 14.7% | 332 s |

By hour the test window covers 12:00 to 17:00. The range holds 80% to 83% from 12:00 to 14:00 and falls to 78.7% at 15:00 and 76.8% at 16:00, and the misses are lopsided: in the afternoon rush the vehicle ends up later than the range about 14% to 18% of the time and earlier only about 6%. The calibration window (07:00 to 12:00) shows the same in the morning rush (76.8% at 07:00, 16.4% missed above). So the range is about right overall but too narrow, and too optimistic, in the rush hours; a per-hour or per-horizon calibration would need more days. Nothing here covers the evening, the night or a weekend. The full tables are in [docs/ml/results.md](docs/ml/results.md); the two charts below show the same.

<p align="center">
  <img src="docs/ml/calibration_by_horizon.svg" width="640" alt="Interval coverage by scheduled time to the stop">
  <img src="docs/ml/calibration_by_hour.svg" width="640" alt="Interval coverage by hour of the day">
</p>

All figures above come from the training run itself, on its exact folds; the card records the pull time, the fold fractions, the moment spacing and the tree cap, so `make ml-credibility` can rebuild the same folds.

### What the model knows and does not know

Knows, with figures from the held-out window:
- How a delay tends to evolve over the next 1 to 12 stops of a trip on a weekday, given the vehicle's latest delay, how other vehicles on the line and at the stop are doing, and the vehicle ahead. It beats the best baseline by 11.2% of MAE (83.8 s to 74.4 s), most for the nearest stops (18% for stops under 2 minutes of scheduled time away).
- How uncertain it is: the 80% interval covered 79.8% overall and 78.7% to 82.9% at every distance, but only about 77% in the afternoon rush.

Does not know:
- Anything outside weekdays of late October: no weekend, holiday, winter, or disruption. The gain may be different or gone there; the interval may be too narrow there, as it already is in the rush hours.
- Weather, events, road works and traffic: none is an input.
- True arrival times: labels are interpolated between vehicle reports.
- More than 12 stops ahead, or a vehicle whose reports are older than 120 s: the baseline answers there and the interval and reason are absent.
- Why a delay happens. The reason line lists what moved the number, not causes.

The same summary is served at `/api/model` and shown in the app under "About the model" (English and Polish).

**Explainability.** Global importance (mean absolute SHAP value on the test window) and partial dependence of the four strongest features, produced by the training command:

<p align="center">
  <img src="docs/ml/importance.svg" width="720" alt="Global feature importance">
</p>

<p align="center">
  <img src="docs/ml/partial_dependence.svg" width="720" alt="Partial dependence of the four strongest features">
</p>

Read the partial dependence with care: the strongest features are correlated (stops ahead and scheduled running time say nearly the same), so their individual curves share one effect. It shows what the model learned, not causes.

**Limitations, plainly.**
- Three and a half weekdays, tested on one afternoon. The margin could be different, or vanish, on a weekend, in winter or on a day with a disruption.
- The gain is real but moderate: about 9 s of MAE on an 83.8 s best baseline, on afternoons when vehicles were around five minutes late and kept getting later.
- Delays are derived (interpolated) and the model is evaluated against these derived times, not against a ground-truth feed; the labels and the vehicle-reported delay come from the same pipeline.
- Report age: training and serving use the same cap (120 s) and the same random-moment sampling, so there is no designed mismatch, but the age distribution was measured on one evening and the collector's polling (20 s) adds up to that much to a report's age in live use. Held answers (up to 60 s) were not evaluated separately; they repeat an answer of the same model.
- No horizon beyond 12 stops (the baseline answers there), no holiday or weather information, one fixed 80% interval calibrated overall, not per line or per horizon.

**Retraining.** Everything below runs on the maintainer's machine; the server only reads the resulting file.

Training needs numpy, scipy and lightgbm, which are installed by hand, outside the project:

```bash
pip install numpy scipy lightgbm        # out-of-tree: not in pyproject.toml or uv.lock
```

> **Licence note.** These wheels bundle LGPL/GPL runtime libraries (libquadmath, libgfortran). Training is done on the maintainer's machine, nothing is distributed or installed on the server, and the training libraries are deliberately not part of the project's dependencies. (numpy is also pulled in by shapely, an existing runtime dependency; that is unchanged by this feature.) `make ml-train` prints this and exits if the libraries are missing.

```bash
REROUTE_SSH="ssh -p 8022 user@server" make ml-pull   # or DATABASE_URL=... make ml-pull
make ml-train                           # writes backend/models/delay_model.json and docs/ml/*
make ml-test                            # all ML tests; the ones that need the libraries skip without them
make ml-frozen                          # score the committed model, unchanged, on later data
make ml-credibility                     # add ridge and interval calibration to the card (exact folds)
make ml-fixtures                        # regenerate the golden test fixtures (needs the libraries)
```

Memory grows with the number of simulated predictions: one day of Warsaw data (about 0.7 M arrivals, one prediction per minute of each trip) peaks near 4 GB. For several days on a 16 GB machine, `python -m ml train --moment-every 240 ...` simulates one prediction per four minutes instead; neighbouring minutes of a trip are nearly the same sample, so little is lost. `--fractions` puts the fold boundaries where whole service periods fit; the card records both, and `make ml-credibility` reuses them.

The server-side tests, including the proof that the pure-Python evaluator reproduces LightGBM's predictions and SHAP values, run in CI without LightGBM: they compare against golden values stored in `backend/tests/fixtures/ml_golden.json` (generated by `backend/scripts/make_ml_fixtures.py`, together with a small synthetic model file used by the API tests). The tests that train a model, build samples or compare live inputs with training inputs need the libraries and are skipped in CI with a clear reason; they run on the maintainer's machine.

The model file is picked up by path when the API starts: `DELAY_MODEL_FILE` (default `backend/models/delay_model.json`). With nothing set the API serves the model only if its card says it was promoted; `PREDICTOR=model` forces it, `PREDICTOR=package.module:Class` selects another predictor. The version stamp (data date and a hash of the trees) is returned in every response as `model.version`. A migration adds a small BRIN index on `stop_arrival_events(actual_at)` so the live inputs (the last hour of arrivals of a few lines and stops) are cheap to read.

**What would make it better as data grows.** Several weeks would allow weekday, hour and holiday features, per-line and per-stop effects (a target encoding of how much each stop segment typically adds), a validation scheme with several consecutive test windows instead of one, weather as a feature, and a per-horizon calibrated interval. Retraining on a rolling window is one command; the next honest check is whether the margin holds on a day the model has never seen.

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
