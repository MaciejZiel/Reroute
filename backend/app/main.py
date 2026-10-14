"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from .api import router
from .api_live import router as live_router
from .database import AdminSession, admin_engine, wait_for_database
from .demo_data import seed_demo_network
from .migrate import migrate
from .model_card import router as model_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_database(admin_engine)
    migrate()
    with AdminSession() as session:
        seed_demo_network(session)
    yield


app = FastAPI(
    title="Reroute API",
    description="Live Warsaw transit: stop departures, line vehicles and delays.",
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
app.include_router(live_router)
app.include_router(model_router)


@app.exception_handler(PoolTimeoutError)
@app.exception_handler(OperationalError)
def database_busy(_: Request, error: Exception) -> JSONResponse:
    """A cancelled slow statement or an exhausted pool is "busy, retry", not a server fault."""
    return JSONResponse(
        {"detail": "The database is busy, try again in a few seconds."},
        status_code=503,
        headers={"Retry-After": "5"},
    )
