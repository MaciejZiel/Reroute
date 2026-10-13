-- Tables written by the collector. Observations and arrival events are range partitioned;
-- the collector creates and drops the partitions (app/collector/partitions.py).

-- Only what is needed to rebuild arrival events and to train on: about 100 bytes per row with
-- the primary key, ~5.5M rows a day for the Warsaw fleet. Everything else (route, mode, next
-- stop id, scheduled time) follows from trip_id, next_stop_seq and the timetable.
CREATE TABLE IF NOT EXISTS vehicle_observations (
    observed_at   timestamptz NOT NULL,
    vehicle_id    text        NOT NULL,
    trip_id       text,
    line          text        NOT NULL,
    lat           real        NOT NULL,
    lon           real        NOT NULL,
    bearing       smallint,
    -- 1-based position in the trip of the stop the vehicle is heading to
    next_stop_seq smallint,
    delay_s       integer,
    -- 0 ok, 1 no trip, 2 trip unknown, 3 off route, 4 layover, 5 implausible
    match_status  smallint    NOT NULL,
    -- 0 live poll, 1 backfill from raw snapshots
    source        smallint    NOT NULL DEFAULT 0,
    PRIMARY KEY (observed_at, vehicle_id)
) PARTITION BY RANGE (observed_at);

-- What actually happened at each stop of each trip: the training table. No secondary indexes:
-- they would cost as much as the rows; per-stop analysis scans a monthly partition.
CREATE TABLE IF NOT EXISTS stop_arrival_events (
    service_date  date        NOT NULL,
    trip_id       text        NOT NULL,
    -- 1-based position of the stop in the trip
    stop_seq      smallint    NOT NULL,
    stop_id       text        NOT NULL,
    line          text        NOT NULL,
    scheduled_at  timestamptz NOT NULL,
    actual_at     timestamptz NOT NULL,
    delay_s       integer     NOT NULL,
    -- seconds between the two observations the crossing was interpolated from
    gap_s         smallint    NOT NULL,
    PRIMARY KEY (service_date, trip_id, stop_seq)
) PARTITION BY RANGE (service_date);

-- Latest position of every vehicle, read by the API instead of fetching the feed itself.
CREATE UNLOGGED TABLE IF NOT EXISTS vehicle_latest (
    vehicle_id    text PRIMARY KEY,
    observed_at   timestamptz NOT NULL,
    label         text,
    trip_id       text,
    service_date  date,
    route_id      text,
    line          text        NOT NULL,
    mode          text        NOT NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    bearing       smallint,
    next_stop_seq smallint,
    next_stop_id  text,
    scheduled_s   integer,
    delay_s       integer,
    match_status  smallint    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_vehicle_latest_line ON vehicle_latest (line);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        text PRIMARY KEY,
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    active_from     timestamptz,
    active_to       timestamptz,
    cause           text NOT NULL,
    effect          text NOT NULL,
    header_pl       text,
    header_en       text,
    description_pl  text,
    description_en  text,
    url             text
);
CREATE INDEX IF NOT EXISTS ix_alerts_last_seen ON alerts (last_seen);

CREATE TABLE IF NOT EXISTS alert_entities (
    id        bigserial PRIMARY KEY,
    alert_id  text NOT NULL REFERENCES alerts (alert_id) ON DELETE CASCADE,
    route_id  text,
    stop_id   text,
    trip_id   text
);
CREATE INDEX IF NOT EXISTS ix_alert_entities_alert ON alert_entities (alert_id);
CREATE INDEX IF NOT EXISTS ix_alert_entities_route ON alert_entities (route_id);
CREATE INDEX IF NOT EXISTS ix_alert_entities_stop ON alert_entities (stop_id);

-- Small key/value store for the collector (backfill progress).
CREATE TABLE IF NOT EXISTS collector_state (
    key         text PRIMARY KEY,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
