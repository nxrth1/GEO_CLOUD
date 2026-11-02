import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/gis_jobs")

# The unified, shared base that all models must inherit from

POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "5"))
POOL_TIMEOUT = int(os.getenv("DATABASE_POOL_TIMEOUT", "30"))


Base = declarative_base()

engine = create_engine(DATABASE_URL, pool_size=POOL_SIZE, max_overflow=MAX_OVERFLOW, pool_timeout=POOL_TIMEOUT)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
