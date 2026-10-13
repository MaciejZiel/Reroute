#!/bin/sh
# Copy recorded data to CSV files for training.
#
#   ml/pull.sh <arrivals.csv> [<observations.csv>]
#
# Reads from the database in DATABASE_URL (psql), or through SSH when REROUTE_SSH is set
# (for example REROUTE_SSH="ssh -p 8022 user@host"; psql then runs on the server, so no database
# credentials ever leave it). Arrivals are what the model learns from; observations (only
# trip, time and the delay the vehicle reported) give it the vehicle's latest delay and are
# kept 7 days by the collector. OBSERVATION_DAYS limits how far back they are copied (default 7).
set -eu
arrivals="${1:?usage: ml/pull.sh <arrivals.csv> [<observations.csv>]}"
observations="${2:-}"
days="${OBSERVATION_DAYS:-7}"

run() {
  if [ -n "${REROUTE_SSH:-}" ]; then
    $REROUTE_SSH "psql -d reroute -c \"$1\""
  else
    psql "${DATABASE_URL:?set DATABASE_URL or REROUTE_SSH}" -c "$1"
  fi
}

# With observations, copy only the arrivals inside the same window: older arrivals would have no
# vehicle delay and would silently be trained on a different base.
window=""
if [ -n "$observations" ]; then window="WHERE actual_at > now() - interval '$days days'"; fi
run "\\copy (SELECT service_date, trip_id, stop_seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s FROM stop_arrival_events $window ORDER BY service_date, trip_id, stop_seq) TO STDOUT WITH CSV HEADER" > "$arrivals"
wc -l "$arrivals" >&2
if [ -n "$observations" ]; then
  run "\\copy (SELECT trip_id, observed_at, delay_s FROM vehicle_observations WHERE trip_id IS NOT NULL AND delay_s IS NOT NULL AND match_status = 0 AND observed_at > now() - interval '$days days' ORDER BY trip_id, observed_at) TO STDOUT WITH CSV HEADER" > "$observations"
  wc -l "$observations" >&2
fi
