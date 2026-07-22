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

## MVP implementation

```text
OpenELIS FHIR → OHS FHIR Data Pipes → governed semantic marts/views
  → Catalyst → med-agent-hub profile → local read-only demo execution → table
  → optional med-agent-hub report profile
```

The query-to-table sandbox implements that path with:

- a pinned synthetic multi-analyte OpenELIS cohort;
- HAPI FHIR backfill and pinned FHIR Data Pipes full/incremental pipelines;
- a PostgreSQL `analytics.lab_result_fact_v1` semantic view and catalog;
- Hub-owned Gemma and Qwen generation/review profiles selectable in the UI,
  with SQL roles fixed at temperature zero and DRY repetition penalty zero;
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

The recommended configuration connects the containerized Hub to the existing
host llama.cpp router at `http://host.docker.internal:8077` and selects the
revision-capable `catalyst-query-gemma-4-12b` profile: Gemma 4 12B writes and
Qwen 2.5 14B reviews. Both exact model IDs must be served.
Open the sidecar at `http://localhost:3000`.

To use a different OpenAI-compatible server, set its root without a trailing
`/v1` together with the exact model and Hub profile IDs:

```bash
export MVP_MODEL_BACKEND=external
export MVP_EXTERNAL_ROUTER_URL=http://host.docker.internal:1234
export MVP_EXTERNAL_MODEL_ID=my-exact-model-id
export MVP_EXTERNAL_PROFILE_ID=my-hub-profile-id
# For a multi-model profile, map every exact role model for this backend:
export MVP_EXTERNAL_EXPECTED_ROLE_MODELS_JSON='{"query_generate":"writer-id","query_review":"reviewer-id"}'
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

The server's advertised model IDs must exactly match a configured Hub profile.
The UI lists only profiles whose required models are currently served.

An optional bundled fallback downloads Qwen2.5-Coder 1.5B and advertises its
truthful `qwen2.5-coder-1.5b-instruct-q4_k_m` identity:

```bash
export MVP_MODEL_BACKEND=local
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

Recorded proof: [download the MVP Playwright video](docs/assets/catalyst-query-to-table-mvp.webm).

Deterministic fake-backend mode:

```bash
export MVP_MODEL_BACKEND=fake
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

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
