# AGENTS.md

## Cursor Cloud specific instructions

### Repository purpose and authority

Catalyst is the OpenELIS reporting integration service. The target architecture
makes Catalyst a client of med-agent-hub:

```text
OpenELIS / analytics source → Catalyst → med-agent-hub → Catalyst table/report response
```

R0–R4 target a local demo with demo data and local LLMs. Production security
and real clinical data are explicitly future work.

Canonical architecture and planning:

- `docs/specification.md`
- `docs/roadmap.md`
- `docs/med-agent-hub.md`

Do not duplicate product architecture in this file. This file is an environment
and test runbook.

The current code still implements the earlier OGC-70 prototype:
Gateway → RouterAgent → CatalystAgent → Catalyst-local LLM, plus a standalone
MCP server. Treat Router/SchemaAgent/SQLGenAgent and Catalyst-local provider
selection as migration scaffolding, not the target design.

### Toolchain

- **Python 3.11** (pinned in `.python-version`; install with
  `uv python install 3.11`)
- **uv** package manager
- A separate `pyproject.toml`, `uv.lock`, and `.venv` under each of
  `catalyst-gateway/`, `catalyst-agents/`, and `catalyst-mcp/`

### First-time local setup

```bash
cp env.recommended .env
mkdir -p logs
cd catalyst-gateway && uv sync --frozen --extra dev && cd ..
cd catalyst-agents && uv sync --frozen --extra dev && cd ..
cd catalyst-mcp && uv sync --frozen --extra dev && cd ..
```

### Running services

#### Full stack

Use this for OpenELIS, Catalyst, and med-agent-hub integration work. It requires
Docker and Docker Compose v2.20 or newer because the compose file uses
`include`.

```bash
cp env.recommended .env
./scripts/bootstrap-deps.sh
./scripts/full-stack-up.sh
./scripts/full-stack-health.sh
```

Or manually:

```bash
./scripts/bootstrap-deps.sh
docker compose -f docker-compose.full-stack.yml up -d --build
```

| Service | URL |
| --- | --- |
| OpenELIS UI | `https://localhost/` (`admin` / `adminADMIN!`) |
| OpenELIS DB | `localhost:15432` |
| Catalyst Gateway | `http://localhost:8000/health` |
| med-agent-hub | `http://localhost:8080/health` |

Bootstrap creates:

- `.openelis-docker/` from
  [`DIGI-UW/openelis-docker`](https://github.com/DIGI-UW/openelis-docker)
- `.med-agent-hub/` from
  [`pmanko/med-agent-hub`](https://github.com/pmanko/med-agent-hub)

All services share `openelis-network`.

The full-stack compose currently proves service co-location and health. Do not
claim that Catalyst calls the hub until the R1 hub-client path and its
integration tests exist.

#### Current Catalyst prototype

From the repository root:

```bash
./catalyst-agents/.venv/bin/honcho -f Procfile.dev start
```

This starts:

- Gateway `:8000`
- RouterAgent `:9100`
- CatalystAgent `:9101`
- MCP `:9102`

Alternative:

```bash
docker compose -f catalyst-dev.docker-compose.yml up -d
```

This is the legacy standalone stack. It does not start med-agent-hub,
OpenELIS, OHS FHIR Data Pipes, SchemaAgent, or SQLGenAgent.

### LLM setup

#### Target

med-agent-hub and its local model router own providers, models, prompts, stage
ordering, review, grounding, and context budgets. Catalyst should eventually
configure only the hub base URL and approved report profile IDs; the v1 query
profile is fixed as `catalyst-query-checked`. See
`docs/med-agent-hub.md`.

#### Current prototype

Until R1 migration, `/v1/chat/completions` uses Catalyst-local configuration:

- `CATALYST_LLM_PROVIDER=lmstudio` with an OpenAI-compatible endpoint at
  `LMSTUDIO_BASE_URL`; or
- `CATALYST_LLM_PROVIDER=gemini` with `GOOGLE_API_KEY`.

Without a provider, health and unit tests pass but the legacy provider E2E does
not.

Inside the full-stack compose, the hub uses `HUB_LLM_BASE_URL`, normally
pointing to a local model router through `host.docker.internal`.

### Tests and lint

CI-equivalent checks, run in each component:

```bash
uv run ruff format --check .
uv run ruff check .
PYTHONPATH=. uv run pytest tests/ -v
```

Full current-prototype smoke suite:

```bash
./tests/run_tests.sh all
```

The smoke script requires a repository-root `.env` because `Procfile.dev`
passes it to Uvicorn.

Legacy provider E2E:

```bash
./tests/e2e/test_provider_e2e.sh
```

Do not use `tests/e2e/test_multiagent_e2e.sh` as evidence of the target
architecture. It covers the older Catalyst-local agent topology.

For roadmap implementation:

- use test-driven vertical slices;
- test the real Catalyst → hub boundary before milestone sign-off;
- validate query result correctness against seeded analytics data, not only SQL
  shape;
- keep mock-only tests as component evidence, not end-to-end evidence.

### Gotchas

- `catalyst-dev.docker-compose.yml` is Catalyst-only. Use
  `docker-compose.full-stack.yml` for OpenELIS and med-agent-hub.
- First full-stack startup may pull large OpenELIS images. The web application
  can lag database readiness.
- `tests/run_tests.sh` starts Honcho and tears it down on exit. Start Honcho
  separately for manual API testing.
- `MCP_DB_ENABLED` defaults to false; current MCP schema data is mocked.
- Current multi-agent mode expects SchemaAgent on the same default port used by
  MCP and does not pass SchemaAgent output into SQLGenAgent. It is legacy
  scaffolding, not a supported target mode.
- `catalyst-agents/Makefile` targets apply only to the agents component.
- The required `catalyst-query-checked` med-agent-hub profile is planned but
  does not exist yet. Never document or test it as shipped until `/v1/models`
  advertises it.

### Evaluation boundary

Local golden queries are engineering fixtures for MVP development. The
Clinical AI Validation Harness transition is intentionally deferred until the
query-to-table MVP exit criteria pass. Do not update or depend on the external
harness during R0–R3 work unless the roadmap is explicitly revised.
