# Demo deployment

`docker-compose.demo.yml` is a complete, self-contained Catalyst stack: the
gateway, the UI, an analytics database, a local model router, and a Caddy edge
proxy. It is the reference for how Catalyst is deployed, and it boots from a
bare clone of this repository — nothing in it reaches into a superproject.

It is a *demo* stack in one specific sense: the analytics database is restored
from a `pg_dump` snapshot rather than populated by the real fhir-data-pipes
ingestion, so there are no OpenELIS/OpenMRS containers here. Everything from
the gateway inward is the real query path.

## Prerequisites

Two inputs are too large for version control and must be supplied:

- **A model.** Put the GGUF the router serves in `./models/`. The compose file
  names the file it expects in the `model-router` command.
- **A seeded analytics database.** Restore a dump into the `analytics-db`
  service, or point `CATALYST_ANALYTICS_DSN` at a database that already has
  the `analytics` schema from `analytics/sql/`.

One input is a separate repository:

- **med-agent-hub**, which builds from source. The default context assumes a
  sibling clone at `../med-agent-hub`; override with `MED_AGENT_HUB_CONTEXT`.
  `MED_AGENT_HUB_REVISION` must name the commit that checkout sits at — the hub
  validates it at startup and refuses to run without it, so an image built
  without it builds cleanly and then exits 1.

  Once the hub publishes an image this collapses to an `image:` pin and both
  variables go away.

## Boot

```bash
MED_AGENT_HUB_REVISION=$(git -C ../med-agent-hub rev-parse HEAD) \
  docker compose -f docker-compose.demo.yml up -d
```

Serves plain HTTP on `:80`. Set `CATALYST_SITE` to a domain to get automatic
Let's Encrypt TLS instead:

```bash
CATALYST_SITE=catalyst.example.org docker compose -f docker-compose.demo.yml up -d
```

## Routing

Caddy is the only host-facing service. It routes by path so the API and the SPA
share one origin (no CORS) while staying independently restartable:

| Path | Service |
| --- | --- |
| `/v1/catalyst/*` | `catalyst-gateway:8000` |
| everything else | `catalyst-ui:8080` (SPA) |

Rebuilding the UI does not take the API down with it. The long read timeout on
the API route matters: a generated query can hold the model for as long as
`CATALYST_HUB_TIMEOUT_SECONDS`, and any proxy in front with a shorter timeout
returns 504 before the answer arrives.

The UI image also carries an nginx `/v1/catalyst/` proxy. That path is for
stacks with no edge proxy in front (`docker-compose.mvp.yml`); in this stack
Caddy matches first and nginx never sees those requests.

## Additional data sources

The stack boots with the built-in OpenELIS source only, which is what lets it
run from a bare clone. Registering another source needs three things that live
outside this repository — its own analytics database, a generated catalog
describing that database, and a registry entry naming both:

```bash
CATALYST_EXTRA_SOURCES_DIR=/path/to/source-dir \
  docker compose -f docker-compose.demo.yml \
                 -f docker-compose.demo.extra-sources.yml up -d
```

The directory is mounted at `/app/config/extra`, and its `data-sources.json`
addresses catalogs as `/app/config/extra/<path-within-that-directory>`. So a
directory laid out as

```
source-dir/
  data-sources.json
  catalog/my-source-catalog.json
```

declares `"catalogPath": "/app/config/extra/catalog/my-source-catalog.json"`.

Each catalog must declare a `datasetBrowser` profile naming its fact view and
which columns carry the subject, category, value, unit, and timestamps.
Without one the source is still queryable, but its dataset cannot be browsed —
the browser reports which source is unconfigured rather than guessing another
source's column names.
