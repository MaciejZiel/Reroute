#!/bin/sh
# Copy recorded arrivals to a CSV for training.
#
#   ml/pull.sh <out.csv>                      from the database in DATABASE_URL (psql)
#   REROUTE_SSH="ssh -p 8022 user@host" ml/pull.sh <out.csv>   through SSH, psql on the server
#
# Only the arrival events are exported (no credentials, no positions of individual vehicles
# beyond what the public feed carries).
set -eu
out="${1:?usage: ml/pull.sh <out.csv>}"
copy="\\copy (SELECT service_date, trip_id, stop_seq, stop_id, line, scheduled_at, actual_at, delay_s, gap_s FROM stop_arrival_events ORDER BY service_date, trip_id, stop_seq) TO STDOUT WITH CSV HEADER"
if [ -n "${REROUTE_SSH:-}" ]; then
  $REROUTE_SSH "psql -d reroute -c \"$copy\"" > "$out"
else
  psql "${DATABASE_URL:?set DATABASE_URL or REROUTE_SSH}" -c "$copy" > "$out"
fi
wc -l "$out" >&2
