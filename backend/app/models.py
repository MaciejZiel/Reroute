"""Transit network, position snapshots, and saved simulations."""

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
        ForeignKey("stops.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)


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


class SimulationRecord(Base):
    __tablename__ = "simulation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(80))
    disruption_type: Mapped[str] = mapped_column(String(32))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class FeedMetadata(Base):
    __tablename__ = "feed_metadata"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feed_start_date: Mapped[str] = mapped_column(String(16), default="")
    feed_end_date: Mapped[str] = mapped_column(String(16), default="")
    feed_version: Mapped[str] = mapped_column(String(48), default="")
    attributions: Mapped[str] = mapped_column(Text, default="")
