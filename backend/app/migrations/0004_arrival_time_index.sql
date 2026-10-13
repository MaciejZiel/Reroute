-- The delay model reads the last hour of arrivals of a few lines and stops. Rows are inserted in
-- roughly time order, so a block-range index on actual_at is tiny and costs almost nothing to
-- maintain (a b-tree would be as large as the rows, which is why the table has none).
CREATE INDEX IF NOT EXISTS ix_stop_arrival_events_actual_at
    ON stop_arrival_events USING brin (actual_at);
