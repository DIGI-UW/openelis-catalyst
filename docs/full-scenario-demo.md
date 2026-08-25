# The full-scenario demo: one spec, two modes

`catalyst-ui/e2e/full-scenario-demo.spec.ts` walks the product's whole claim
through the product's own path: a plain-language laboratory question becomes
checked SQL in the workbench, is refined in conversation, both results are
saved as governed Datasets, a table Widget and a grouped-bar Widget are built
over them, a Dashboard collects both, `Publish to Superset` writes the native
bundle, the pinned importer brings it in, and the finished dashboard renders
in Superset.

It runs two ways, and they are **the same steps**:

| | project | video | dwells | what it is for |
|---|---|---|---|---|
| **e2e** | `deterministic` | off | none | does the whole path still work end to end |
| **video** | `demo-video` | 1280×720 | yes | raw footage for a published cut |

The only difference is `dwell()`, which holds the frame long enough to read
something and is a no-op outside the video project. A demo that diverges from
the test stops being evidence that the product works.

## Prerequisites

The live stack, including Superset (which does **not** come back on its own
after a host restart):

```sh
docker start catalyst-mvp-isolated-superset-metadata-db catalyst-mvp-isolated-superset
```

Two invariants that cost real debugging time when violated (see the demo
issue log for the full stories):

- **One checkout.** The gateway's outbox/receipts mounts, and the checkout
  the importer runs from, must be the same tree. A gateway recreated from a
  different worktree silently reads an empty outbox and never shows
  `Imported`.
- **The Superset port.** The importer stamps the public URL into its receipt;
  that becomes the product's own "Open Superset" link. Run it with
  `SUPERSET_PORT` matching the published port (18088 on the isolated stack)
  or the link 404s.

## Running it

```sh
cd catalyst-ui

# as a test
PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
  CATALYST_STACK_DIR=<running stack checkout>/targets/catalyst \
  CATALYST_STACK_OVERRIDE=../../compose/catalyst-mvp-isolated.override.yml \
  npx playwright test e2e/full-scenario-demo.spec.ts --project=deterministic

# as a recording
…same env… npx playwright test e2e/full-scenario-demo.spec.ts --project=demo-video
```

The spec runs the pinned importer itself (`e2e/support/superset-import.ts`),
so the "Superset bundle ready → Imported" flip happens on camera and the e2e
mode genuinely covers the seam.

The recording lands at `test-results/*/video.webm` and is **wiped by the next
run** — copy it out immediately. Milestones land in
`demo-milestones/full-scenario-demo.json`; the published cut's timeline is
authored from them (`scripts/author_timeline.py` in the harness repo, plan in
`e2e/full-scenario-demo.plan.json`) and rendered by
`scripts/render_demo_video.py` — see `specs/demo-video-recording-guide.md`
there.

## Environment

| variable | default | meaning |
|---|---|---|
| `PLAYWRIGHT_LIVE` | — | must be `true`; otherwise the spec skips |
| `PLAYWRIGHT_BASE_URL` | `http://127.0.0.1:4173` | the Catalyst UI |
| `PLAYWRIGHT_SUPERSET_URL` | `http://127.0.0.1:18088` | Superset, for the final act |
| `CATALYST_STACK_DIR` | repo root | the RUNNING stack's checkout (targets/catalyst) |
| `CATALYST_STACK_OVERRIDE` | — | compose override file, e.g. the isolated stack's |
| `CATALYST_STACK_PROJECT` | `catalyst-mvp-isolated` | compose project of the running stack |
| `CATALYST_SUPERSET_PORT` | `18088` | published Superset port, stamped into receipts |
| `SUPERSET_ADMIN_USERNAME` / `_PASSWORD` | `admin` / `admin` | Superset sign-in |
