# Catalyst

Catalyst is the OpenELIS reporting integration service. Its target architecture
uses med-agent-hub for all LLM profile, model-team, prompt, review and grounding
behavior while Catalyst owns trusted data integration, deterministic query
policy, execution and table output.

The current target is a local demo using demo data and local LLMs. Production
security and real clinical data are future roadmap work.

The first product milestone is:

> Natural-language question → reviewed query → table output

## Canonical documentation

- [Product specification](docs/specification.md)
- [Roadmap](docs/roadmap.md)
- [med-agent-hub client contract](docs/med-agent-hub.md)
- [Cloud development instructions](AGENTS.md)

The original
[OGC-70 specs](https://github.com/DIGI-UW/OpenELIS-Global-2/tree/develop/specs/OGC-070-catalyst-assistant)
are historical planning inputs. The local specification records which
capabilities are retained, reassigned, superseded or deferred.

## Current implementation status

The current source is the OGC-70 prototype:

```text
Gateway → RouterAgent → CatalystAgent → local LLM client
                                └────→ mock schema
MCP server → mock approved schema + deterministic SQL checks
```

The target is:

```text
OpenELIS FHIR → OHS FHIR Data Pipes → governed semantic marts/views
  → Catalyst → med-agent-hub profile → local read-only demo execution → table
  → optional med-agent-hub report profile
```

The repository does not yet implement the hub-backed query profile, analytics
views, query execution or table UI. Existing Router/SchemaAgent/SQLGenAgent
code remains migration scaffolding until roadmap work replaces it.

## Repository layout

- `catalyst-gateway/` — OpenAI-compatible HTTP boundary.
- `catalyst-agents/` — current OGC-70 agent prototype and future hub-client
  migration surface.
- `catalyst-mcp/` — deterministic schema/context and SQL policy tools.
- `docs/` — canonical local specification and roadmap.
- `scripts/` — dependency bootstrap and full-stack helpers.
- `tests/` — smoke scripts and local golden-query fixtures.

OpenELIS backend and frontend integration live in
[`DIGI-UW/OpenELIS-Global-2`](https://github.com/DIGI-UW/OpenELIS-Global-2).
med-agent-hub lives in
[`pmanko/med-agent-hub`](https://github.com/pmanko/med-agent-hub).

## Local setup

Requirements: Python 3.11 and
[`uv`](https://docs.astral.sh/uv/).

```bash
cp env.recommended .env
mkdir -p logs

cd catalyst-gateway && uv sync --frozen --extra dev && cd ..
cd catalyst-agents && uv sync --frozen --extra dev && cd ..
cd catalyst-mcp && uv sync --frozen --extra dev && cd ..
```

Run the current prototype stack:

```bash
./catalyst-agents/.venv/bin/honcho -f Procfile.dev start
```

This starts Gateway `:8000`, Router `:9100`, CatalystAgent `:9101`, and MCP
`:9102`. It does not represent the final hub-client topology.

## Full stack

Docker Compose v2.20 or newer is required.

```bash
cp env.recommended .env
./scripts/bootstrap-deps.sh
./scripts/full-stack-up.sh
./scripts/full-stack-health.sh
```

Services:

- OpenELIS UI: `https://localhost/`
- Catalyst health: `http://localhost:8000/health`
- med-agent-hub health: `http://localhost:8080/health`

The compose topology currently co-locates the services. Application-level
Catalyst → hub query integration is roadmap work.

## Tests

Per component:

```bash
uv run ruff format --check .
uv run ruff check .
PYTHONPATH=. uv run pytest tests/ -v
```

Repository smoke suite:

```bash
./tests/run_tests.sh all
```

Provider and multi-agent E2E scripts under `tests/e2e/` validate the legacy
prototype. They will be replaced by hub profile contract and query-to-table
tests during the hub-client migration.

## Evaluation

`tests/fixtures/golden_queries.json` remains a local engineering fixture for
the query-to-table MVP. It is not evidence of clinical validation.

After the MVP exit criteria in the roadmap pass, evaluation should move to the
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness).
That external update is intentionally deferred.
