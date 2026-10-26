# Design: live Warsaw transit map with a data pipeline for delay prediction

Status: proposal, to be implemented in stacked pull requests (see "Delivery plan").

## 1. Purpose and scope

Reroute answers one question: **is my tram or bus coming, and is it late?**

Core interaction (unchanged from the product brief):

1. Click a stop (or search by name) and see every line that serves it with the next scheduled
   departures and, where a live vehicle of that trip can be matched, the live delay and ETA.
2. Click a line to highlight its route shape and show only that line's live vehicles.
   Clicking a vehicle shows its delay against the timetable and its next stops.
3. Clear/back returns to the full map.

Underneath, the app becomes a **data pipeline** that records every vehicle observation and
derives stop arrival events from them, so a delay-prediction model can be trained later and
dropped into a stable API contract. Everything that does not serve this (the disruption
simulator and its graph/routing code) is removed.

Non-goals: training a model in this change, passenger counts, trip planning.

## 2. Findings that shape the design

- The Warsaw vehicle feed (`vehicles.pb`) carries ~1,900 vehicles per poll, a vehicle `timestamp`
  (vehicles update every ~10-30 s), `trip_id`, position, bearing, **no** `route_id`, **no**
  `current_stop_sequence`, **no** delay. Delays must be derived (projection onto the trip's stops).
- Trip ids embed the service date: `2026-10-13:140:PcS:2:1543` (`<service date>:<line>:...`). The
  static GTFS has one trip per line per service date, so "the trips running today" is a date
  prefix, not a calendar join, and the live trip id maps 1:1 onto a stored timetable.
  The importer already stores every trip of the feed window (`trip_schedules`, ~270k trips) as a
  stop pattern id plus a comma-separated departures string; it has no date column, no headsign
  and no index to find the trips calling at a stop.
- GTFS times can exceed 24:00:00. They are seconds after "noon minus 12 h" of the service date
  (not midnight), which matters on DST days (23 h / 25 h days). All instants are therefore computed
  as `datetime.combine(service_date, 12:00, Europe/Warsaw) - 12 h + seconds`, never as
  `midnight + seconds`.
- The service-alerts feed (`alerts.pb`) exists next to the vehicle feed.
- Raw 30 s snapshots of `vehicles.pb` are already archived on the developer machine as
  `~/reroute-data/raw/<YYYY-MM-DD>/v_<HHMMSS>.pb.gz` (UTC, ~160 KB each, ~460 MB/day
  compressed). They are the replayable source of truth for backfill.

## 3. Data model

All timestamps are `timestamptz` (UTC). `service_date` is the Warsaw local service day taken from
the trip id. Migrations are plain numbered SQL files applied by `python -m app.migrate`
(table `schema_migrations`); `create_all` stops being the schema owner. No new dependency.

### 3.1 Static timetable (changed)

| Table | Change |
| --- | --- |
| `trip_schedules` | add `service_date date`, `headsign text`, `direction_id smallint`; index `(pattern_id, service_date)` and `(route_id, service_date)` |
| `stop_patterns` | unchanged (ordered stop ids per pattern) |
| `pattern_stops` (new) | `(pattern_id, stop_id, position)` unnested from `stop_patterns` (~60k rows), index on `stop_id`. Lets "trips calling at stop S" be an index lookup instead of scanning patterns |
| `routes`, `stops`, `route_stops` | unchanged; `route_stops(stop_id)` index stays |
| `route_segments`, `simulation_records` | removed (only the simulator used them) |

Next departures at a stop = `pattern_stops` (stop -> patterns) join `trip_schedules` for
`service_date in (yesterday, today)` and parse only the matching departure out of the string.
Expected ~1-3k candidate trips for a busy interchange, no new giant table.

### 3.2 `vehicle_observations` (new, partitioned by day)

One row per *new* feed position of a vehicle (a row is skipped when the vehicle's own timestamp has
not changed since the previous poll, so 20 s polling does not multiply rows).

| Column | Notes |
| --- | --- |
| `observed_at` | the vehicle's own timestamp (partition key) |
| `vehicle_id`, `trip_id`, `line` | `trip_id` NULL for vehicles without a trip |
| `lat`, `lon`, `bearing` | `real` coordinates (~0.5 m) |
| `next_stop_seq` | 1-based position in the trip of the stop the vehicle is heading to |
| `delay_s` | `observed_at` minus the scheduled instant at the projected point; NULL when not computable |
| `match_status` | smallint enum: 0 ok, 1 no trip, 2 trip unknown, 3 off route, 4 layover, 5 implausible |
| `source` | 0 live, 1 backfill |

Route, mode, service date, next stop id and scheduled time follow from `trip_id`,
`next_stop_seq` and the timetable, so they are not stored (the first version stored them and
needed ~255 B/row; the measured slim row is ~146 B/row including the primary key).

- Primary key `(observed_at, vehicle_id)` (partition key first); `ON CONFLICT DO NOTHING` makes
  live polling and backfill idempotent and safe to overlap. There is deliberately no secondary
  index: a `(trip_id, observed_at)` index cost as much as the rows themselves.
- Daily range partitions created on demand by the collector; **retention 7 days by default**
  (`OBSERVATION_RETENTION_DAYS`), enforced by `DROP TABLE` of whole partitions.
- Measured on the real feed: ~4,500 new observations a minute (live, 24 min sample), ~6.5M rows and ~0.95 GB a day,
  so 7 days is ~6.5 GB.

### 3.3 `stop_arrival_events` (new, derived, monthly partitions)

The ML training table: what actually happened at each stop of each trip.

| Column | Notes |
| --- | --- |
| `service_date`, `trip_id`, `stop_seq` | primary key (partition key `service_date`) |
| `stop_id`, `line` | denormalised for queries |
| `scheduled_at` | scheduled instant (DST-safe, see section 2) |
| `actual_at` | interpolated time the vehicle passed the stop |
| `delay_s` | `actual_at - scheduled_at` |
| `gap_s` | seconds between the two observations the crossing was interpolated from (quality: small is good) |

Derivation: the collector keeps the last observation of each vehicle (rebuilt from
`vehicle_latest` at restart). When a new observation advances the projected progress along the
trip past one or more stops, each crossed stop gets `actual_at` linearly interpolated (in
progress, not in distance) between the two observations. Gaps above 600 s produce no events.
The departure from the first stop is not an event (it is hidden in the layover).
Re-derivable from raw snapshots, so the algorithm can improve later. Monthly partitions,
**retention 90 days** by default. Measured ~1.3M events a day at ~170 B each is ~220 MB/day, so
90 days is ~20 GB: on a 40 GB disk set `ARRIVAL_RETENTION_DAYS=60`.

### 3.4 Alerts (new)

- `alerts(alert_id, first_seen, last_seen, active_from, active_to, cause, effect, header_pl, header_en, description_pl, description_en, url)`
- `alert_entities(alert_id, route_id, stop_id, trip_id)`; index on `route_id` and `stop_id`.

The collector polls `alerts.pb` every 60 s and upserts by `alert_id`; `last_seen` going stale
marks an alert as ended. Linking to routes lets features ask "is there an active alert on this
line".

### 3.5 Serving tables

- `vehicle_latest(vehicle_id pk, ...same position/delay/next-stop columns)` is UNLOGGED and
  upserted by the collector every poll. The web API reads it instead of fetching the
  feed itself, so a page load never waits on the upstream feed and N API workers do not poll N
  times. If `vehicle_latest` is older than 90 s (collector not running, e.g. plain local dev)
  the API falls back to the current in-process fetch, so `docker compose up` of just db+api still works.
- `delay_snapshots` stays as the per-line, per-minute rollup behind the punctuality view; the
  collector writes it once a minute from `vehicle_latest` (7-day retention) instead of the API.

## 4. Collector service

A separate process and container, same code base and image as the API, different command:
`python -m app.collector run`.

Responsibilities per loop (default every 20 s, `POLL_INTERVAL_S`, floor 10 s):

1. Fetch `vehicles.pb` with `gtfs-rt-kit` (MIT, by the same author, `GtfsRtClient`: timeouts,
   retry/backoff) and decode with `gtfs_rt_kit.parse_feed`; optionally write the raw bytes to
   `RAW_ARCHIVE_DIR/<YYYY-MM-DD>/v_<HHMMSS>.pb.gz` (same layout as today's archive, off by default).
2. `derive`: resolve line/mode, load timetables of the trips in the feed (cached per `trip_id`,
   not re-queried every poll), project onto the trip, compute next stop, scheduled time, delay, status.
3. Write: batch insert into `vehicle_observations`, upsert `vehicle_latest`, derive and insert
   `stop_arrival_events`, write the per-minute rollup. One transaction per poll.
4. Housekeeping: create tomorrow's partitions, drop expired ones, poll alerts, refresh the
   trip cache when `feed_metadata.downloaded_at` changes.

`derive` is a pure function of (feed, previous state, timetable lookup) and has **no network or
clock dependency**; the live loop and the backfill both call it, so they cannot diverge.

Backfill: `python -m app.collector backfill --raw-dir ~/reroute-data/raw --from 2026-10-13 [--to ...]`
reads `v_*.pb.gz` in time order, decodes, runs the same `derive` with the feed header
timestamp as the clock and writes with `source=1`. Incremental: the last processed file is kept in
`collector_state` per archive directory, so a repeated run only reads new files (its vehicle state is primed from
the last processed file). Idempotent (primary key conflicts are
ignored); the static timetable must contain the trips of those dates (the report lists
how many trip ids could not be found).

Operations:

- Config by environment: `DATABASE_URL`, `GTFS_RT_URL`, `ALERTS_URL`, `POLL_INTERVAL_S`,
  `OBSERVATION_RETENTION_DAYS`, `ARRIVAL_RETENTION_DAYS`, `RAW_ARCHIVE_DIR`, `STORE_UNMATCHED`, `METRICS_PORT`,
  `SCHEDULE_REFRESH_HOURS` (re-import a static timetable older than this, see
  `app/collector/schedule_refresh.py`; a stale one matches no realtime trip after a few days).
- Health and metrics on `METRICS_PORT` (default 9100, stdlib `http.server` in a thread, no new
  dependency): `/healthz` returns 200 only if the last successful poll is younger than 3 x interval;
  `/metrics` in Prometheus text format (polls ok/failed, feed age, vehicles, observations
  written, matched share, arrival events written, last poll duration, loop lag).
- Failure handling: an upstream error is logged and retried on the next tick with capped
  backoff; a DB error rolls back the poll and reconnects; SIGTERM finishes the current poll
  and exits 0. Docker `restart: unless-stopped`.
- Footprint target for a small server (2 vCPU, 4 GB): collector < 150 MB RSS, < 5 % of one core.

## 5. ML seam

Two modules with stable contracts; the model itself comes later.

`app/features.py` (pure, unit-tested, SQL separated from math):

```python
@dataclass(frozen=True)
class FeatureRow:
    trip_id: str; stop_sequence: int; stop_id: str; route_id: str
    scheduled_at: datetime
    current_delay_s: int | None        # latest observation of this trip
    delay_trend_s_per_min: float | None  # slope over the last 10 min
    previous_stop_delay_s: int | None  # last completed stop event of the trip
    stops_remaining: int
    scheduled_headway_s: int | None
    hour_local: int; weekday: int; is_holiday: bool
    route_mean_delay_s_15m: float | None
    active_alerts: int
```

plus `python -m app.features export --from D --to D --out file.jsonl` producing training rows
(features at time t, target = `delay_s` of the arrival event) from `stop_arrival_events` and
`vehicle_observations`, using only data available before t (no leakage).

`app/predict.py`:

```python
class Predictor(Protocol):
    name: str; version: str
    def predict(self, rows: Sequence[FeatureRow]) -> list[Prediction]: ...
```

`BaselinePredictor` (persistence): predicted delay = current delay of the trip (0 when the
vehicle is not matched), interval NULL. `PREDICTOR=module:Class` selects another implementation
at startup. Without it `get_predictor()` looks for the model file (`DELAY_MODEL_FILE`, default
`backend/models/delay_model.json`) and serves `app.ml.model.ModelPredictor` if the file's card says
the model beat the baselines on held-out future data; `PREDICTOR=model` forces it. `FeatureRow`
carries `model_inputs`, computed from recorded arrivals by `app/ml/featurize.py`, the same code
that builds the training samples in `backend/ml`. The model, its evaluation and its limits are
described in the README (Delay model).

Endpoint contract (stub now, final shape):

```
GET /api/predictions?stop_id=<id>[&line=<short_name>][&limit=10]
GET /api/predictions?trip_id=<id>

200 {
  "generated_at": "2026-10-13T14:00:00Z",
  "model": {"name": "persistence", "version": "1", "kind": "baseline"},
  "predictions": [{
    "trip_id": "...", "line": "140", "mode": "bus", "stop_id": "700223", "stop_sequence": 12,
    "scheduled_at": "...", "predicted_at": "...",
    "predicted_delay_s": 120, "lower_delay_s": null, "upper_delay_s": null,
    "basis": "live_delay | scheduled"
  }]
}
```

The stop panel's ETA uses this endpoint's logic from the first PR, so the UI does not change when
the model arrives. A `prediction_log(predicted_at, trip_id, stop_id, model_version, predicted_delay_s)` table
(added with the first non-baseline model, not now) will allow scoring predictions against later
`stop_arrival_events`.

## 6. Public API (core UX)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/stops?q=&limit=` | name search over an in-memory index of the ~7k stops (rebuilt per import), case and diacritics folded, prefix matches first; results are *stations*: platforms with the same name within 400 m |
| `GET /api/stops/{id}/departures?scope=&per_line=&horizon_minutes=` | `scope=station` (default) covers all platforms of the stop, `platform` only the clicked one; lines at the stop, each with next departures (scheduled, live delay/ETA when a vehicle of the trip is matched); `at=` for tests |
| `GET /api/lines/{line}` | route shapes (per direction) and stops of the line |
| `GET /api/lines/{line}/vehicles` | live vehicles of the line with delay and next stops |
| `GET /api/vehicles` | all live vehicles (map), from `vehicle_latest` |
| `GET /api/vehicles/{id}` | one vehicle: delay, next stops with scheduled/predicted times |
| `GET /api/predictions` | contract above |
| `GET /api/model` | the served model's card: version, windows, test metrics, interval calibration (static, from the model file) |
| `GET /api/punctuality` | kept, secondary |
| `GET /api/network`, `/api/health` | kept |

All responses are typed with Pydantic models in `schemas.py`; the stop panel target is
p95 < 300 ms on the real network (measured and reported in the PR).

## 7. What is removed

- `POST /api/simulations`, `routing.py`, the graph cache, `RouteSegment`, `SimulationRecord`,
  `analyse_disruption` and the segment builder in the importer, `scripts/benchmark_api.py`
  (replaced by a benchmark for the new endpoints), `test_routing.py`, `test_simulation_analysis.py`.
- Frontend: the scenario/disruption panel and its types and strings, the rerouting screenshots.
- README sections on rerouting, graph decisions and the old benchmark.
- `collect_delays.py` (replaced by the collector).
- Kept: demo network (offline first run), punctuality view, bilingual UI, map.
- The PR that removes these is separate, so the removal is easy to review and revert.

## 8. Deployment shape

```
 browser --> web (nginx: static frontend, proxies /api) --> api (uvicorn) --> db (PostGIS)
                                                              collector ---->  db
                                                              collector --> vehicles.pb, alerts.pb
```

- `docker-compose.yml`: `db`, `migrate` (one-shot `python -m app.migrate`, api and collector
  depend on it), `api`, `collector`, `web`. The dev compose keeps the Vite dev server;
  `docker-compose.prod.yml` swaps it for nginx serving the built bundle. All services
  `restart: unless-stopped`, healthchecks, a named volume for Postgres and a bind mount for the optional raw archive.
- One backend image, two commands (`uvicorn ...`, `python -m app.collector run`).
- Small server: 2 vCPU / 4 GB / 40 GB disk handles 7 days of observations and 90 days of arrival
  events; Postgres tuned with `shared_buffers=512MB`, `max_wal_size=2GB` in the compose command.
  Backups: `pg_dump` of everything except `vehicle_observations` partitions.
- Secrets only via `.env`; the DB is not published on a host port in the prod compose file.

## 9. Third-party code

- `gtfs-rt-kit` (MIT) becomes a backend dependency (decode + HTTP client). It is recorded in
  `3rdparty_licenses.md` with its license link. It replaces the hand-written protobuf handling
  in `data_sources.py`. Its `protobuf>=7.34` requirement must resolve together with
  `gtfs-realtime-bindings` (checked in the first PR; `gtfs-realtime-bindings` is dropped if
  the kit's decoding covers everything we need).
- No other new Python or npm dependencies are planned (migrations, metrics, search and the
  collector loop use the standard library).

## 10. Delivery plan (stacked PRs, each reviewable on its own)

1. **Data layer**: migrations, new tables and partitions, importer additions (`service_date`,
   headsign, `pattern_stops`), `3rdparty_licenses.md`, this document.
2. **Collector**: `derive`, live loop, backfill from raw snapshots, health/metrics, Dockerfile
   command and compose service, tests (DST, 24:xx times, idempotent backfill).
3. **API**: stops search, departures, line, vehicles, predictions stub, `features.py`, tests,
   measurements on the real timetable.
4. **Frontend**: stop panel, line mode, vehicle popup, search, mobile layout, PL/EN strings.
5. **Cleanup and docs**: remove the simulator, update README with real screenshots, prod compose.

## 11. Risks and open questions

- Observation volume is an estimate; the first PR measures a real hour and adjusts retention and the `STORE_UNMATCHED` default.
- Projection onto straight stop-to-stop lines can mis-assign progress on loops (a line that
  passes the same stop twice). Mitigation: restrict the projection to a window around the last
  known progress of the trip, and mark `off_route`/`implausible` rather than guess.
- Trip ids with the date prefix are a property of the mkuran.pl feed. If the format changes the importer falls back to `calendar.txt`/`calendar_dates.txt` for `service_date` (already parsed today).
- Existing databases need a re-import to fill `service_date`, `headsign` and `pattern_stops`; the API reports "timetable needs re-import" (HTTP 503 with a clear message) instead of returning empty results.
