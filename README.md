# Catalyst

Catalyst is the OpenELIS reporting integration service. For governed query
generation, med-agent-hub owns the shared profile registry, prompts,
role-to-model mapping, and model knobs. Catalyst owns catalog/context assembly,
writer/reviewer orchestration, deterministic validation, execution, query
lineage, and table output, and invokes each configured Hub role by profile ID.

The current target is a local demo using demo data and local LLMs. Production
security and real clinical data are future roadmap work.

The first product milestone is:

> Natural-language question → governed query → table output

That query/notebook milestone is accepted. The selected next milestone is:

> Governed execution → Dataset draft → Widget draft → Dashboard draft →
> deterministic native bundle → local Superset 6.1.0 renderer

Catalyst remains the supervised builder and desired-configuration source for
this one-way MVP. `Publish to Superset` writes/downloads a bundle from a shared
outbox; stack bootstrap or an explicit CLI helper imports it. The Superset REST
API and bidirectional reconciliation are deferred.

## Canonical documentation

- [Product specification](docs/specification.md)
- [Roadmap](docs/roadmap.md)
- [Dashboard Builder design](docs/dashboard-builder-mvp-design.md)
- [med-agent-hub client contract](docs/med-agent-hub.md)
- [Cloud development instructions](AGENTS.md)

The original
[OGC-70 specs](https://github.com/DIGI-UW/OpenELIS-Global-2/tree/develop/specs/OGC-070-catalyst-assistant)
are historical planning inputs. The local specification records which
capabilities are retained, reassigned, superseded or deferred.

## MVP implementation

```text
OpenELIS FHIR → OHS FHIR Data Pipes → governed semantic marts/views
  → Catalyst query engine → med-agent-hub role executor → local model router
  → Catalyst local read-only demo execution → table
  → optional med-agent-hub report profile
```

The query-to-table sandbox implements that path with:

- a pinned synthetic multi-analyte OpenELIS cohort;
- HAPI FHIR backfill and pinned FHIR Data Pipes full/incremental pipelines;
- a PostgreSQL `analytics.lab_result_fact_v1` semantic view and catalog;
- a Hub-owned Catalyst query profile selectable in the UI only when its exact
  Gemma and Qwen role models are advertised by the external router;
- writer → deterministic lint/correction → optional reviewer → deterministic
  re-lint orchestration in Gateway, with exact role/model/prompt/configuration
  evidence;
- deterministic Catalyst SQL policy, explicit preview acceptance, read-only
  execution, typed table contracts, and declared/effective model provenance;
- a switchable second data source (OpenMRS HIV/ART) alongside OpenELIS —
  its own analytics database and catalog, targetable per turn within one
  session (`GET /v1/catalyst/data-sources`);
- a React/Carbon sidecar UI with deterministic and live-model Playwright tests.

The original `/v1/chat/completions` and Router/Agent/MCP code remain available
as legacy compatibility scaffolding; the MVP does not use them.

## Repository layout

- `catalyst-gateway/` — OpenAI-compatible HTTP boundary.
- `catalyst-ui/` — React/Carbon query review and results sidecar.
- `analytics/` — OpenELIS seed, Data Pipes configuration, semantic SQL, and
  approved catalog.
- `catalyst-agents/` — legacy OGC-70 agent prototype.
- `catalyst-mcp/` — deterministic schema/context and SQL policy tools.
- `docs/` — canonical local specification and roadmap.
- `scripts/` — dependency bootstrap and full-stack helpers.
- `tests/` — smoke scripts and local golden-query fixtures.

OpenELIS backend and frontend integration live in
[`DIGI-UW/OpenELIS-Global-2`](https://github.com/DIGI-UW/OpenELIS-Global-2).
med-agent-hub lives in
[`pmanko/med-agent-hub`](https://github.com/pmanko/med-agent-hub).

## Local setup

Requirements: Python 3.11, [`uv`](https://docs.astral.sh/uv/), Node.js 22,
Docker, and Docker Compose v2.20+.

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

## Query-to-table MVP

Docker Compose v2.20 or newer is required.

```bash
cp env.recommended .env
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

The configuration connects the containerized Hub to the existing host
OpenAI-compatible router at `http://host.docker.internal:1234`. The Hub-owned
`catalyst-query-e4b-qwen14b` profile uses `google/gemma-4-e4b` for writing and
`qwen2.5-14b-instruct-mlx` for review. Startup fails unless the router
advertises both exact IDs. Open the sidecar at `http://localhost:3000`.

To use the real router at another location, set its root without a trailing
`/v1` and keep the Hub-owned profile ID explicit:

```bash
export MVP_MODEL_BACKEND=external
export MVP_EXTERNAL_ROUTER_URL=http://host.docker.internal:1234
export MVP_EXTERNAL_PROFILE_ID=catalyst-query-e4b-qwen14b
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

The profile ID must exist in med-agent-hub's shared `server/levels.yaml`
catalog. Hub discovery combines that configuration with its credential-free
router inventory. Gateway exposes only available query profiles, and rejects an
unavailable selection before creating a session or invoking a model. A backend
can still change after discovery, so generation/backend failure remains a
supported residual path.

Recorded proof: [download the MVP Playwright video](docs/assets/catalyst-query-to-table-mvp.webm).

Stop or reset disposable demo state:

```bash
./scripts/mvp-down.sh
./scripts/mvp-reset.sh
```

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

MVP evidence:

```bash
./tests/e2e/test_mvp_live.sh
./tests/e2e/test_data_pipes_incremental.sh
cd catalyst-ui
npx playwright test --project=deterministic
PLAYWRIGHT_BASE_URL=http://localhost:3000 \
PLAYWRIGHT_USE_MOCK_API=false \
PLAYWRIGHT_QUERY='Show viral load results since 2026-01-01 with value, unit, release date, and receipt-to-release time' \
npx playwright test --project=demo-video e2e/query-to-table.spec.ts
```

## Evaluation

The seeded MVP and its local golden scenarios are engineering evidence, not
clinical validation.

The automated component and mocked-browser gates pass. Live G2.8c acceptance
with the Gemma 4 12B writer, Qwen 2.5 14B reviewer, and an independent
PostgreSQL result comparison remains pending. The
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness)
is now the active umbrella integration and experiment path.
