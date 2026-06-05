# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is

Catalyst (OGC-70) is a Python microservices stack for OpenELIS Global: Gateway → Router → Catalyst Agent → MCP. The default dev mode is single-agent (`CATALYST_AGENT_MODE=single`).

### Toolchain

- **Python 3.11** (pinned in `.python-version`; install via `uv python install 3.11`)
- **uv** package manager (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Each component has its own `pyproject.toml` + `uv.lock` virtualenv under `catalyst-{gateway,agents,mcp}/.venv`

### First-time / local setup

```bash
cp env.recommended .env
mkdir -p logs
cd catalyst-gateway && uv sync --frozen --extra dev && cd ..
cd catalyst-agents && uv sync --frozen --extra dev && cd ..
cd catalyst-mcp && uv sync --frozen --extra dev && cd ..
```

### Running services

From repo root, start all four core services (Gateway :8000, Router :9100, Catalyst :9101, MCP :9102):

```bash
./catalyst-agents/.venv/bin/honcho -f Procfile.dev start
```

Alternative: `docker compose -f catalyst-dev.docker-compose.yml up -d` (requires Docker).

### LLM provider (required for `/v1/chat/completions` E2E)

`.env` defaults to `CATALYST_LLM_PROVIDER=lmstudio` with `LMSTUDIO_BASE_URL=http://localhost:1234/v1`. For real provider E2E you need either:

- LM Studio (or compatible OpenAI API) on port 1234, or
- `CATALYST_LLM_PROVIDER=gemini` plus `GOOGLE_API_KEY`

Without an LLM, health checks and unit/smoke tests still pass; chat-completion E2E will fail.

### Tests and lint (CI-equivalent)

Per component (`catalyst-gateway`, `catalyst-agents`, `catalyst-mcp`):

```bash
uv run ruff format --check .
uv run ruff check .
PYTHONPATH=. uv run pytest tests/ -v
```

Full smoke suite (starts Honcho services, waits for health, runs all pytest suites):

```bash
./tests/run_tests.sh all
```

Provider E2E (services must already be running + LLM available):

```bash
./tests/e2e/test_provider_e2e.sh
```

### Gotchas

- `run_tests.sh` starts Honcho in the background and tears it down on exit; for manual API testing, start Honcho separately and keep it running.
- PostgreSQL (`MCP_DB_ENABLED`) is off by default in M0.x; MCP uses mock schema data.
- Multi-agent mode (M0.2+) requires SchemaAgent and SQLGenAgent, which are not in `Procfile.dev` by default.
- `catalyst-agents/Makefile` targets (`make check`, `make dev-setup`) apply only to the agents component.
