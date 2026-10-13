"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .api_live import router as live_router
from .database import SessionLocal
from .demo_data import seed_demo_network
from .migrate import migrate


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
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
app.include_router(live_router)
