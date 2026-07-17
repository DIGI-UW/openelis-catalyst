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
- Hub-owned Gemma and Qwen generation/review profiles selectable in the UI;
- deterministic Catalyst SQL policy, expiring preview acceptance, read-only
  execution, typed table contracts, and provenance;
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

The first live run downloads and verifies the local Qwen2.5-Coder 1.5B GGUF.
Open the sidecar at `http://localhost:3000`.

To use an already-running OpenAI-compatible server instead of the bundled
router, set the server root without a trailing `/v1`:

```bash
MVP_MODEL_BACKEND=external \
MVP_HUB_LLM_BASE_URL=http://host.docker.internal:1234 \
./scripts/mvp-up.sh
./scripts/mvp-seed.sh
./scripts/mvp-health.sh
```

The server's advertised model ID must exactly match a configured Hub profile.
The UI marks profiles unavailable until their required model is served.

Recorded proof: [download the MVP Playwright video](docs/assets/catalyst-query-to-table-mvp.webm).

Deterministic fake-backend mode:

```bash
MVP_FAKE_BACKEND=true ./scripts/mvp-up.sh
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

The MVP exit criteria now pass. The next evaluation phase should move to the
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness).
That external update is intentionally deferred.
