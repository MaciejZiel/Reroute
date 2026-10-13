-- Trip dates, headsigns and lookups for "which trips call at this stop".
-- Idempotent: fresh databases get these objects from the models via create_all.
ALTER TABLE IF EXISTS trip_schedules ADD COLUMN IF NOT EXISTS service_date date;
ALTER TABLE IF EXISTS trip_schedules ADD COLUMN IF NOT EXISTS headsign text NOT NULL DEFAULT '';
ALTER TABLE IF EXISTS trip_schedules ADD COLUMN IF NOT EXISTS direction_id smallint;
CREATE INDEX IF NOT EXISTS ix_trip_schedules_pattern_date ON trip_schedules (pattern_id, service_date);
CREATE INDEX IF NOT EXISTS ix_trip_schedules_route_date ON trip_schedules (route_id, service_date);
CREATE INDEX IF NOT EXISTS ix_route_stops_stop_id ON route_stops (stop_id);
ALTER TABLE IF EXISTS routes ALTER COLUMN shape
    TYPE geometry(Geometry, 4326) USING shape::geometry(Geometry, 4326);
