FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY catalyst-gateway/pyproject.toml /app/pyproject.toml
COPY catalyst-gateway/uv.lock /app/uv.lock
COPY catalyst-gateway/README.md /app/README.md
RUN uv sync --frozen --no-dev --no-install-project

COPY catalyst-gateway/src /app/src
COPY docs/contracts /docs/contracts

CMD ["uv", "run", "uvicorn", "src.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
