import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router
from .request_limits import RequestBodyLimitMiddleware

from .logging_config import setup_logging
setup_logging(service_name="api")

logger = logging.getLogger(__name__)

app = FastAPI(
    title="GEOSPATIAL_PROCESSES",
    description="Geospatial processing with FastAPI",
    version="1.0.0",
)

configured_origins = os.getenv("GEO_CLOUD_CORS_ORIGINS")
allowed_methods = [method.strip()
    for method in os.getenv(
        "GEO_CLOUD_CORS_METHODS",
        "GET,POST,PUT,PATCH,DELETE"
    ).split(",")]
allowed_headers = [header.strip()
    for header in os.getenv(
        "GEO_CLOUD_CORS_HEADERS",
        "Authorization,Content-Type,Accept"
    ).split(",")]
origins = (
    [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
    if configured_origins
    else [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
)

app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=allowed_methods,
    allow_headers=allowed_headers,
)

app.include_router(router)
