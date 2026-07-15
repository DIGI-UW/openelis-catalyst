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

#### Full stack (OpenELIS + Catalyst) — recommended for integration testing

Requires **Docker** and **Docker Compose v2.20+** (`include` support).

```bash
cp env.recommended .env
./scripts/full-stack-up.sh          # clones openelis-docker -> .openelis-docker/, then compose up
./scripts/full-stack-health.sh      # wait for DB + Catalyst gateway
./scripts/full-stack-down.sh        # tear down
```

Or manually:

```bash
./scripts/bootstrap-openelis.sh
docker compose -f docker-compose.full-stack.yml up -d --build
```

| Service | URL |
|---------|-----|
| OpenELIS UI | https://localhost/ (admin / adminADMIN!) |
| OpenELIS DB | localhost:15432 (`clinlims` / password from `.openelis-docker/.env`) |
| Catalyst Gateway | http://localhost:8000/health |

OpenELIS is sourced from [DIGI-UW/openelis-docker](https://github.com/DIGI-UW/openelis-docker) (shallow clone to `.openelis-docker/`). Catalyst services join the `openelis-network` so MCP can reach `db.openelis.org`.

Inside containers, LLM calls use `host.docker.internal:1234` (override via `LMSTUDIO_BASE_URL` in `.env`).

#### Catalyst only (no OpenELIS)

Honcho from repo root (Gateway :8000, Router :9100, Catalyst :9101, MCP :9102):

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

- **`catalyst-dev.docker-compose.yml` is Catalyst-only** — it does not start OpenELIS. Use `docker-compose.full-stack.yml` for OE + Catalyst.
- First full-stack `up` pulls large OpenELIS images; allow several minutes. OE webapp may lag DB readiness.
- `run_tests.sh` starts Honcho in the background and tears it down on exit; for manual API testing, start Honcho separately and keep it running.
- PostgreSQL (`MCP_DB_ENABLED`) is off by default in M0.x; MCP uses mock schema data even when OE DB is running.
- Multi-agent mode (M0.2+) requires SchemaAgent and SQLGenAgent, which are not in `Procfile.dev` by default.
- `catalyst-agents/Makefile` targets (`make check`, `make dev-setup`) apply only to the agents component.

### med-agent-hub integration (report generation / display)

Catalyst handles **short, synchronous SQL Q&A** (Gateway → Router → Agent → MCP). **Longer, multi-stage lab reports** should delegate to [med-agent-hub](https://github.com/pmanko/med-agent-hub) — a profile-driven clinical answer engine with deterministic validation gates and staged async delivery.

| Concern | Catalyst (this repo) | med-agent-hub |
|---------|----------------------|---------------|
| Primary job | NL → SQL, schema context | NL → validated clinical answer + in-depth report |
| Client API | OpenAI `/v1/chat/completions` → A2A chain | OpenAI `/v1/chat/completions` + `/v1/models` |
| Orchestration | Router + specialist agents | Single hub; stages defined in `server/levels.yaml` |
| Async model | Request/response (honcho) | Same engine; **staged SSE** for product profiles |
| Context | MCP mock / future `clinlims` | Evidence ledger + inline chart or Querystore patient |

#### How to talk to the hub

**Discover profiles**

```bash
curl -fsS http://localhost:8080/v1/models
```

Default product profile: `single-e4b-checked` (fast answer → review → grounded in-depth). Profiles declare stages, models, validation, and `capabilities.staged`.

**Blocking report (drain full pipeline into one JSON string)**

```bash
curl -fsS http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "single-e4b-checked",
    "stream": false,
    "messages": [
      {"role": "user", "content": "[1] (2026-01-15) Test: HIV viral load 1200 copies/mL"},
      {"role": "user", "content": "Summarize recent viral load trends for a lab report."}
    ]
  }'
```

**Staged async (for report display UI)** — set `"stream": true` on a profile with `capabilities.staged: true`. Response is **SSE**, not OpenAI token chunks. Events (in order):

| Event | Purpose |
|-------|---------|
| `answer_done` | Fast draft answer + resolved references (`answerValidation.status` may be `validating`) |
| `answer_validation` | Post-review correction (if review stage edits the draft) |
| `indepth_pending` | In-depth section starting |
| `indepth_done` / `indepth_error` | Detailed claims or failure |
| `done` | Final envelope: answer, inDepth, references, gates |

Heartbeats: `: hb` comments every 10s. Errors: `event: error` with `{code, source, message}`.

**Patient context (Querystore / OpenMRS harness)**

```bash
curl -fsS http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "single-e4b-checked",
    "stream": true,
    "patient": "<patient-uuid>",
    "messages": [{"role": "user", "content": "Generate a lab summary report."}]
  }'
```

Requires hub env: `QUERYSTORE_BASE_URL`, `QUERYSTORE_USERNAME`, `QUERYSTORE_PASSWORD`. Inline numbered chart lines work without Querystore.

**Context source contract (for OpenELIS lab data)**

Hub adapters implement `ContextSource` in `server/context_sources.py`. Evidence records use numbered `[N] (yyyy-mm-dd) text` lines with stable `sourceId` for citation grounding. For Catalyst → hub handoff, serialize OpenELIS query results into that chart shape (see hub `chart_serializer.py`) rather than raw SQL rows.

#### Intended Catalyst + hub split

```text
OpenELIS UI
  -> Catalyst Gateway (SQL / data retrieval, RBAC at execution)
  -> format lab evidence as inline chart or future OE ContextSource
  -> med-agent-hub POST /v1/chat/completions (stream: true, product profile)
  -> UI renders SSE stages (answer → validation → in-depth) as report sections
```

Hub runs separately (`LLM_BASE_URL` points at llama.cpp or other OpenAI-compatible router on ~8077). Not yet wired into `docker-compose.full-stack.yml`; add as a follow-up service once report profiles are pinned for OE.

Reference repo: https://github.com/pmanko/med-agent-hub
