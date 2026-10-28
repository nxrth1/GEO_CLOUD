import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router


app = FastAPI(
    title="GEOSPATIAL_PROCESSES",
    description="Geospatial processing with FastAPI",
    version="1.0.0",
)

configured_origins = os.getenv("GEO_CLOUD_CORS_ORIGINS")
origins = (
    [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
    if configured_origins is not None
    else [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
