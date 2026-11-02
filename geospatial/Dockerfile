FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app \
    && useradd --system --gid app --create-home app \
    && mkdir -p /app/data/datasets \
    && chown -R app:app /app

COPY --chown=app:app geospatial ./geospatial
COPY --chown=app:app alembic.ini .

ENV GEO_CLOUD_DATASETS_DIR=/app/data/datasets

USER app

EXPOSE 8000

CMD ["uvicorn", "geospatial.main:app", "--host", "0.0.0.0", "--port", "8000"]