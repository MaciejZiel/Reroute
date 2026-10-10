"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import models  # noqa: F401 — register SQLAlchemy models before create_all.
from .api import router
from .database import Base, SessionLocal, engine
from .demo_data import seed_demo_network


@asynccontextmanager
async def lifespan(_: FastAPI):
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE IF EXISTS routes ALTER COLUMN shape "
                "TYPE geometry(Geometry, 4326) USING shape::geometry(Geometry, 4326)"
            )
        )
        # create_all only indexes new tables; add indexes introduced later to older databases.
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_route_stops_stop_id ON route_stops (stop_id)")
        )
    with SessionLocal() as session:
        seed_demo_network(session)
    yield


app = FastAPI(
    title="Reroute API",
    description="Local API for exploring Warsaw transit and simulating disruption impacts.",
    version="0.1.0",
    lifespan=lifespan,
)

origins = [
    origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
