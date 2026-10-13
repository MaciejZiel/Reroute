# Reroute

**A live Warsaw transit map that answers one question: is my tram or bus coming, and is it late?**

**[Live demo](https://reroute.maciejzielinski.dev)** · [Status](https://status.maciejzielinski.dev) · [Portfolio](https://maciejzielinski.dev)

Click a stop to see every line serving it with the next timetable departures and, where a live vehicle of that trip can be matched, its delay; pick a line to see its route and its moving vehicles; click a vehicle for its delay against the schedule and its next stops. Underneath, a collector records every vehicle observation and stop arrival, and a small gradient-boosted model trained on that data predicts delays further down the line, with an interval and a plain-words reason (see [Delay model](#delay-model)).

[![CI](https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml/badge.svg)](https://github.com/MaciejZiel/Reroute/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![FastAPI + PostGIS](https://img.shields.io/badge/FastAPI-PostGIS-009688.svg)
![React + MapLibre](https://img.shields.io/badge/React-MapLibre-3178c6.svg)

[![Tram line 24 selected from Rondo Daszyńskiego: route, stops, live vehicles and departures with delays](docs/screenshots/line.png)](https://reroute.maciejzielinski.dev)

## Status and self-hosting

- **Live now:** the map, departures with delays estimated from live positions, line and vehicle views and the per-line punctuality view, at <https://reroute.maciejzielinski.dev> (uptime on the [status page](https://status.maciejzielinski.dev)).
- **Self-hosted:** the public instance runs on a home server, not on a cloud platform.
- **Delay model:** a first model exists and is served by default because it beat the baselines on held-out future data, but it was trained on a single evening of data (see the [model card](#delay-model)). Where the model cannot answer (a trip with no recorded stop yet) the API falls back to "the current delay persists".

## What it does

- **Three steps: stop, line, vehicle.** A progress rail at the top of the panel shows where you are; finished steps are clickable to go back. On narrow screens the panel is a bottom sheet (tap or drag the handle between peek, half and full height).
- **Quiet by default.** No vehicles are drawn until you pick a line. A single switch, "Show all vehicles" (off by default), draws the whole fleet as small unlabelled dots for those who want the city-wide picture; names appear only when zoomed in.
- **Stop panel.** Search by name (diacritics-insensitive) or zoom in and click a stop; see all lines at the station with their next timetable departures. Departures with a matched live vehicle show the delay and the expected time; late vehicles stay listed until they pass, vehicles that already passed disappear.
- **Mode-aware stop icons.** Bus stops are mint, tram stops coral and stops served by both are split; a small glyph says which. Stops are hidden at city zoom (the route lines carry the overview), thin out by collision as you zoom in, get names from zoom 16 and the selected station is highlighted in gold with a single label.
- **Line focus.** Pick a line: the map highlights its route and stops and shows only that line's vehicles, with the line number on each, a heading wedge and the delay colour as the outline. Vehicles glide between the 15-second updates. When you came via a stop, a switch limits the view to "Heading to my stop" (the live vehicles that still have a departure at that stop, so direction is handled by the timetable) or "All of this line". Service alerts are shown.
- **Vehicle detail.** Click a vehicle: delay against the timetable, status (waiting at the first stop, off route, stale position) and the next stops with scheduled and expected times.
- **Delay colours.** Vehicle outlines are green (on time), orange (more than 3 min late), blue (more than 1 min early) or grey (unknown).
- **Punctuality (secondary).** Per-line share of early, on-time and late vehicles and a delay trend, from the per-minute rollup the collector writes.
- **Bilingual, responsive UI** (Polish and English; on narrow screens the panel sits under the map).
- **Data pipeline for prediction.** The collector stores per-vehicle observations, derived stop arrival events and service alerts; `app/features.py` exports leak-free training rows and `/api/predictions` is the contract a trained model plugs into (today: the current delay persists; the ML model is the next step).

| Stop selected | Mobile: stop and line |
| --- | --- |
| ![Rondo Daszyńskiego station with the lines serving it and their next departures](docs/screenshots/stop.png) | ![Line 24 selected on a 400 px wide screen, with the bottom sheet under the map](docs/screenshots/mobile-line.png) |

Screenshots are of the live deployment, taken with `playwright-cli` on 13 October 2026 at 1440 px and 400 px widths.

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
- **Frontend:** React 19, TypeScript, Vite, MapLibre GL, OpenFreeMap vector tiles (OpenStreetMap data) with a custom dark style; oxlint and Vitest
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

Export training rows for a model: `python -m app.features export --from 2026-10-13 --to 2026-10-20 --out rows.jsonl`. The delay model is trained from the arrival table directly, see [Delay model](#delay-model); `PREDICTOR=package.module:ClassName` still plugs in any other predictor (see `backend/app/predict.py`).

Schema changes are plain SQL files in `backend/app/migrations/`, applied at start-up and by `python -m app.migrate`.

## Deployment on a small server

Behind nginx on a self-hosted server, include `deploy/termux/nginx-http.conf` (http block) and `deploy/termux/nginx-location.conf` (server block): per-visitor `limit_req` zones keyed on `CF-Connecting-IP`, stricter for the vehicle and prediction endpoints, which read recent arrivals and may compute an explanation. The configuration was checked with `nginx -t`.

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

The vehicle view shows the same reason as one quiet line under the next stops, with the likely range (the sentence is English in both UI languages). Reasons are exact TreeSHAP values of the median model, computed for the first prediction of a request only (the exact computation is the slow part on a small server). Predictions the baseline answers carry no reason.

### Model card

**What it is.** LightGBM quantile regression (median, 10% and 90% quantiles of the delay change; 200 to 450 trees each, 15 leaves, early stopping), exported to a JSON file of flat tree arrays and evaluated by about 200 lines of pure Python (`backend/app/ml/ensemble.py`), so the server needs no extra dependency (no scikit-learn, no LightGBM, no NumPy; ONNX was not needed). The interval is widened by split-conformal calibration on a separate later fold (it came out as a shift of about 0.006 s: the raw quantile heads were already close to calibrated). Version `20261013-77f128a2`, file `backend/models/delay_model.json` (about 1.1 MB).

**Where the model answers.** Only where it was trained and evaluated: the vehicle has a delay reported at most 45 s ago, its last recorded stop is at most 10 minutes old, and the stop asked about is at most 12 stops ahead (and within the scheduled running time seen in training). Everywhere else (no live vehicle, an old last stop, a stop further away) the baseline answers, with no interval and no reason, and the departure is not marked live because of the model. This is enforced in `app/ml/live.py` and `ModelPredictor` and covered by tests.

**Data.** One service date (Tuesday 2026-10-13), 14:25 to 19:30 UTC (5.08 hours, 16:25 to 21:30 local time, afternoon rush into the evening) of the live Warsaw feed: 221,662 recorded stop arrivals on 9,577 trips, 265 lines and 6,401 stops. That is **one evening of one season, nothing more**: no weekend, no morning rush, no holiday, no bad weather, no autumn-versus-summer difference. Weather was not used (no source with a clear free licence was wired in). Arrival times are themselves interpolated between 20 to 30 s observations.

**Samples and split.** A request can arrive at any moment, so predictions are simulated at moments spread over each trip's life (about every minute, at a random offset after a vehicle report, as live requests are): the origin is the newest arrival known then, the target is a stop 1, 2, 3, 4, 6, 8 or 12 stops further along the same trip that has not been reached yet (1,181,216 samples). The split is by time only, four consecutive windows by fractions of the span (0.68, 0.76, 0.84; chosen up front for a roughly 50-minute test window, not tuned), and a sample belongs to a window only if the prediction moment and the arrival it predicts both lie inside it (so 68,166 samples straddling a boundary are dropped). Train 14:26-17:53 UTC (872,663 samples), early stopping to 18:17 (70,286), interval calibration and best-baseline choice to 18:41 (59,910), test 18:41-19:30 UTC, the last 49 minutes (110,191 samples). Features use only arrivals known at the prediction moment (unit-tested by changing everything that happens later), and the live code computes the inputs with the same functions as training (tested for equality against the database at arbitrary moments, with a vehicle delay).

**Features.** Stops ahead and scheduled running time to the target, and how long ago the last stop was recorded; the vehicle's own latest reported delay and the delay at its last recorded stop and how it changed; the delay trend over 10 min; the mean delay of other vehicles on the line (15 min) and at the target stop (30 min); the delay of the vehicle ahead on the same line at the target stop and its scheduled headway; tram or bus. Hour, weekday and stop number are implemented but switched off (`--calendar-features`): with one evening they can only memorise the clock.

**Results on the held-out later window** (MAE and RMSE in seconds; the intervals come from a bootstrap that resamples whole trips, because arrivals of one trip are strongly dependent; "MAE under 2 min" etc. bin by the scheduled time from the last stop to the target):

| predictor | MAE s (95% CI) | RMSE s (95% CI) | bias s | MAE under 2 min | MAE 2 to 5 min | MAE 5 to 10 min | MAE 10 min or more |
| --- | --- | --- | --- | --- | --- | --- | --- |
| scheduled (zero delay) | 102.6 (95.3 to 110.1) | 157.1 (139.6 to 176.2) | -76.7 | 103.0 | 99.3 | 102.2 | 108.8 |
| persistence (BaselinePredictor) | 44.8 (43.2 to 46.6) | 69.9 (63.7 to 76.4) | -14.1 | 19.1 | 31.3 | 48.7 | 70.0 |
| historical median (line, stop, hour) | 119.9 (114.9 to 125.7) | 151.4 (139.7 to 166.2) | +64.3 | 110.7 | 116.3 | 121.6 | 126.5 |
| calibrated persistence | 48.8 (47.3 to 50.5) | 72.1 (67.1 to 77.7) | +18.6 | 19.4 | 32.6 | 52.7 | 79.8 |
| gradient boosted trees (median) | 41.1 (39.6 to 42.7) | 63.3 (57.8 to 69.6) | -7.6 | 16.5 | 28.3 | 44.7 | 65.2 |

80% interval on the test fold: model coverage 78.6% (77.9% to 79.4%), mean width 126 s; persistence plus empirical residual quantiles: coverage 87.3% (86.7% to 87.9%), mean width 191 s.

The best baseline, chosen on the calibration window and not on the test window, is the existing persistence predictor. The model's MAE is **8.2% lower** (bootstrap interval 7.2% to 9.3%), RMSE about 9% lower. By horizon the relative gain is 14%, 10%, 8% and 7%, so it is largest for the nearest stops. The 80% interval covered 78.6% of test arrivals, slightly below nominal; the persistence-plus-empirical-quantiles interval covers more because it is wider (191 s against 126 s).

How far to trust these numbers: the bootstrap interval is sampling uncertainty inside this one window. It treats trips as independent, so it ignores shocks that hit the whole network at once, and it says nothing about other days. The test window was also looked at more than once while the pipeline was being built (features, the vehicle-delay input and the serving rules were changed after earlier evaluations), so it is not a pristine one-shot holdout. The rule for serving the model by default (at least 5% lower MAE than the best baseline with the bootstrap interval above zero) was written into the code at the start, but the history cannot prove that it was not adjusted; read it as a sanity gate for one window, not as evidence the model generalises. It passed, so the model is the default; had it failed, the file would still be trained and reported but `get_predictor()` would keep the baseline (the card inside the model file says `promoted: false`). The historical-median row is degenerate with one evening: the test hours hardly occur in the training window, so it collapses to a near-global median.

![MAE by horizon, model against persistence](docs/ml/mae_by_horizon.svg)

**Explainability.** Global importance (mean absolute SHAP value on the test window) and partial dependence of the four strongest features, produced by the training command:

![Global feature importance](docs/ml/importance.svg)
![Partial dependence](docs/ml/partial_dependence.svg)

Read the partial dependence with care: the strongest features are correlated (stops ahead and scheduled running time say nearly the same), so their individual curves share one effect. It shows what the model learned, not causes.

**Limitations, plainly.**
- One evening. The margin could be different, or vanish, on a weekend, in the morning rush, in winter or on a day with a disruption. The test window is the last 49 minutes of the evening, so there is some distribution shift inside the evening, but nothing resembling a different day.
- The gain is modest: about 3.7 s of MAE on a 45 s baseline. Persistence is a strong baseline for a delay that changes slowly, and it is the thing to beat.
- Delays are derived (interpolated) and the model is evaluated against these derived times, not against a ground-truth feed; the labels and the vehicle-reported delay come from the same pipeline.
- Training simulates the vehicle report as up to 20 s old (live polling) and serving accepts one up to 45 s old, a small remaining mismatch. When the vehicle report is older than that, the baseline answers.
- No horizon beyond 12 stops (the baseline answers there), no holiday or weather information, one fixed 80% interval calibrated overall, not per line or per horizon.

**Retraining.** Everything below runs on a laptop; the server only reads the resulting file.

```bash
pip install -e 'backend[dev,ml]'        # LightGBM, NumPy, SciPy (training only)
REROUTE_SSH="ssh -p 8022 user@server" make ml-pull   # or DATABASE_URL=... make ml-pull
make ml-train                           # writes backend/models/delay_model.json and docs/ml/*
make ml-test                            # includes: exported model reproduces LightGBM, SHAP and live inputs
```

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
- Tests cover the pure derivation, storage, backfill and API against PostGIS, and the model pipeline (no leakage across the time split, exported model equals LightGBM and SHAP, live inputs equal training inputs); the frontend has unit tests for the vehicle filtering and the palette contrast.

## Data and attribution

- Warsaw schedules, routes and stops are provided by ZTM Warsaw in GTFS, distributed by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The feed's `attributions.txt` is retained and exposed by the API.
- Live positions are published by Miasto Stołeczne Warszawa and distributed as GTFS-Realtime by Mikołaj Kuranowski: <https://mkuran.pl/gtfs/>. The app displays the feed timestamp and city source link; no API key is required. The test fixtures contain a short trimmed recording of this feed and the matching ZTM timetables, with the same attribution.
- Base map: vector tiles from [OpenFreeMap](https://openfreemap.org) (free public instance, no API key, no request limits, commercial use allowed; attribution required and shown on the map), © [OpenMapTiles](https://www.openmaptiles.org/), data © OpenStreetMap contributors (ODbL). The dark style is drawn in `frontend/src/basemap.ts`. If you self-host the tiles, point `url` and `glyphs` there at your own instance.

Source code, comments and technical documentation are in English.

## License

MIT License, see [LICENSE](LICENSE). Transit data fetched from the GTFS feeds remains subject to the terms of its source.
