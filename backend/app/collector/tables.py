"""Core table definitions of the collector's tables (created by the SQL migrations).

They live in their own ``MetaData`` so ``create_all`` never touches them: the partitioned
tables need the DDL in ``migrations/0002_collector_tables.sql``.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
)

metadata = MetaData()

vehicle_observations = Table(
    "vehicle_observations",
    metadata,
    Column("observed_at", DateTime(timezone=True), primary_key=True),
    Column("vehicle_id", Text, primary_key=True),
    Column("trip_id", Text),
    Column("line", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("bearing", SmallInteger),
    Column("next_stop_seq", SmallInteger),
    Column("delay_s", Integer),
    Column("match_status", SmallInteger),
    Column("source", SmallInteger),
)

stop_arrival_events = Table(
    "stop_arrival_events",
    metadata,
    Column("service_date", Date, primary_key=True),
    Column("trip_id", Text, primary_key=True),
    Column("stop_seq", SmallInteger, primary_key=True),
    Column("stop_id", Text),
    Column("line", Text),
    Column("scheduled_at", DateTime(timezone=True)),
    Column("actual_at", DateTime(timezone=True)),
    Column("delay_s", Integer),
    Column("gap_s", SmallInteger),
)

vehicle_latest = Table(
    "vehicle_latest",
    metadata,
    Column("vehicle_id", Text, primary_key=True),
    Column("observed_at", DateTime(timezone=True)),
    Column("label", Text),
    Column("trip_id", Text),
    Column("service_date", Date),
    Column("route_id", Text),
    Column("line", Text),
    Column("mode", Text),
    Column("lat", Float),
    Column("lon", Float),
    Column("bearing", SmallInteger),
    Column("next_stop_seq", SmallInteger),
    Column("next_stop_id", Text),
    Column("scheduled_s", Integer),
    Column("delay_s", Integer),
    Column("match_status", SmallInteger),
)

alerts = Table(
    "alerts",
    metadata,
    Column("alert_id", Text, primary_key=True),
    Column("first_seen", DateTime(timezone=True)),
    Column("last_seen", DateTime(timezone=True)),
    Column("active_from", DateTime(timezone=True)),
    Column("active_to", DateTime(timezone=True)),
    Column("cause", Text),
    Column("effect", Text),
    Column("header_pl", Text),
    Column("header_en", Text),
    Column("description_pl", Text),
    Column("description_en", Text),
    Column("url", Text),
)

alert_entities = Table(
    "alert_entities",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("alert_id", Text),
    Column("route_id", Text),
    Column("stop_id", Text),
    Column("trip_id", Text),
)

collector_state = Table(
    "collector_state",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text),
    Column("updated_at", DateTime(timezone=True)),
)
