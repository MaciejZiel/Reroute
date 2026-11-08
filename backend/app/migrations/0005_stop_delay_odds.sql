-- How a line usually runs at a stop: for each line, platform, day type (weekday, Saturday,
-- Sunday; Warsaw time) and scheduled hour over the last 28 service days, how many arrivals
-- were recorded and how late they were. Read by /api/stops/{id}/odds. A full scan of the
-- arrivals, so it is built empty here and refreshed once a night by the collector
-- (app/odds.py), not on every request.
CREATE MATERIALIZED VIEW IF NOT EXISTS stop_delay_odds AS
WITH recent AS (
    SELECT line, stop_id, delay_s, service_date,
           (scheduled_at AT TIME ZONE 'Europe/Warsaw') AS local_at
    FROM stop_arrival_events
    WHERE service_date > (now() AT TIME ZONE 'Europe/Warsaw')::date - 28
)
SELECT line, stop_id,
       CASE EXTRACT(ISODOW FROM local_at)
           WHEN 6 THEN 'saturday' WHEN 7 THEN 'sunday' ELSE 'weekday'
       END AS day_type,
       EXTRACT(HOUR FROM local_at)::smallint AS hour,
       count(*)::int AS n,
       count(DISTINCT service_date)::smallint AS days,
       count(*) FILTER (WHERE delay_s > 180)::int AS late3,
       count(*) FILTER (WHERE delay_s > 300)::int AS late5,
       count(*) FILTER (WHERE delay_s < -60)::int AS early1,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY delay_s)::int AS median_s,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY delay_s)::int AS p90_s,
       min(service_date) AS first_day,
       max(service_date) AS last_day
FROM recent
GROUP BY 1, 2, 3, 4
WITH NO DATA;

-- Unique, so the nightly refresh can run CONCURRENTLY (readers are never blocked).
CREATE UNIQUE INDEX IF NOT EXISTS ux_stop_delay_odds
    ON stop_delay_odds (stop_id, line, day_type, hour);
