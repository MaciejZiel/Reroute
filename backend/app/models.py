"""Transit network, position snapshots, and saved simulations."""

from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    short_name: Mapped[str] = mapped_column(String(32), index=True)
    long_name: Mapped[str] = mapped_column(String(240), default="")
    mode: Mapped[str] = mapped_column(String(24), index=True)
    shape: Mapped[object | None] = mapped_column(Geometry(srid=4326), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    location: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class RouteStop(Base):
    __tablename__ = "route_stops"

    route_id: Mapped[str] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    stop_id: Mapped[str] = mapped_column(
        ForeignKey("stops.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)


class StopPattern(Base):
    """Ordered, comma-separated stop ids shared by trips that call at the same stops."""

    __tablename__ = "stop_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    stop_ids: Mapped[str] = mapped_column(Text)


class PatternStop(Base):
    """One row per stop of a stop pattern: finds the patterns (and so trips) calling at a stop."""

    __tablename__ = "pattern_stops"

    pattern_id: Mapped[int] = mapped_column(
        ForeignKey("stop_patterns.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[str] = mapped_column(String(80), index=True)


class TripSchedule(Base):
    """Compact timetable of one scheduled trip, matched to live vehicles by trip id."""

    __tablename__ = "trip_schedules"
    __table_args__ = (
        Index("ix_trip_schedules_pattern_date", "pattern_id", "service_date"),
        Index("ix_trip_schedules_route_date", "route_id", "service_date"),
    )

    trip_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    route_id: Mapped[str] = mapped_column(String(80), index=True)
    pattern_id: Mapped[int] = mapped_column(Integer)
    # Comma-separated departures in seconds after service-day midnight, one per pattern stop.
    departures: Mapped[str] = mapped_column(Text)
    # Warsaw service day the departures count from; NULL when the feed does not reveal it.
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    headsign: Mapped[str] = mapped_column(Text, default="", server_default="")
    direction_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class DelaySnapshot(Base):
    """Per-line delay summary of one live positions snapshot."""

    __tablename__ = "delay_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    line: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(24))
    vehicles: Mapped[int] = mapped_column(Integer)
    mean_delay_seconds: Mapped[int] = mapped_column(Integer)
    median_delay_seconds: Mapped[int] = mapped_column(Integer)
    early: Mapped[int] = mapped_column(Integer)
    on_time: Mapped[int] = mapped_column(Integer)
    late: Mapped[int] = mapped_column(Integer)


class VehiclePosition(Base):
    __tablename__ = "vehicle_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_number: Mapped[str] = mapped_column(String(48), index=True)
    line: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(24), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    location: Mapped[object] = mapped_column(Geometry("POINT", srid=4326))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class FeedMetadata(Base):
    __tablename__ = "feed_metadata"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feed_start_date: Mapped[str] = mapped_column(String(16), default="")
    feed_end_date: Mapped[str] = mapped_column(String(16), default="")
    feed_version: Mapped[str] = mapped_column(String(48), default="")
    attributions: Mapped[str] = mapped_column(Text, default="")
