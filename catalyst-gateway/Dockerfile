FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY catalyst-gateway/pyproject.toml /app/pyproject.toml
COPY catalyst-gateway/uv.lock /app/uv.lock
COPY catalyst-gateway/README.md /app/README.md
RUN uv sync --frozen --no-dev --no-install-project

COPY catalyst-gateway/src /app/src
COPY docs/contracts /docs/contracts
COPY analytics/catalog /app/config

ENV CATALYST_CATALOG_PATH=/app/config/analytics-catalog-v1.json

CMD ["uv", "run", "uvicorn", "src.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
