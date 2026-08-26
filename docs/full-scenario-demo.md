# The full-scenario demo: one spec, two modes

`catalyst-ui/e2e/full-scenario-demo.spec.ts` walks the accepted visible workflow
through the product's own path: a plain-language laboratory question becomes
checked SQL in the workbench, is refined in conversation, both results are
saved as governed Datasets, a table Widget and a grouped-bar Widget are built
over them, a Dashboard collects both, `Publish to Superset` writes the native
bundle, the pinned importer brings it in, and the finished dashboard renders
in Superset. Until the Spark reference deployment is accepted, this proves the
interaction and publication seam; it is not Phase 1 connection or comparison
evidence.

It runs two ways, and they are **the same steps**:

| | project | video | dwells | what it is for |
|---|---|---|---|---|
| **e2e** | `deterministic` | off | none | does the whole path still work end to end |
| **video** | `demo-video` | 1280×720 | yes | raw footage for a published cut |

The only difference is `dwell()`, which holds the frame long enough to read
something and is a no-op outside the video project. A demo that diverges from
the test stops being evidence that the product works.

## Prerequisites

Start and check the isolated stack through the Clinical AI Validation Harness
operator wrapper. `up` retains the existing databases; do not use `boot`,
`seed`, or `reset` between takes:

```sh
cd <clinical-ai-validation-harness checkout>
./scripts/catalyst-mvp.sh up
./scripts/catalyst-mvp.sh health
```

The scenario gives every Dataset, Widget, and Dashboard a run-specific name,
so reruns are safe against the retained Dashboard Builder state. It does not
reset or reseed the stack. Omit `CATALYST_DEMO_RUN_ID` for the unique default;
if you set it for a named recording, use a fresh value for every take.

The same Harness checkout must own both the running Gateway and this run. Its
wrapper verifies the pinned Catalyst checkout and supplies the isolated
override, project name, ports, and sibling Hub context. This is why the demo
accepts the Harness root rather than reconstructing those settings itself.

## Running it

```sh
cd catalyst-ui

# as a test
PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
  CATALYST_HARNESS_DIR=<running clinical-ai-validation-harness checkout> \
  npx playwright test e2e/full-scenario-demo.spec.ts --project=deterministic

# as a recording
…same env… npx playwright test e2e/full-scenario-demo.spec.ts --project=demo-video
```

The spec runs the pinned importer itself (`e2e/support/superset-import.ts`)
through the Harness's supported `scripts/catalyst-mvp.sh superset-import`
wrapper. That wrapper starts and waits for the required Superset services, so
the "Superset bundle ready → Imported" flip happens on camera and the e2e mode
genuinely covers the seam.

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
| `CATALYST_HARNESS_DIR` | — | required root of the Harness checkout that owns the running isolated stack |
| `CATALYST_DEMO_RUN_ID` | a random UUID | unique suffix for this run's retained builder artifacts |
| `SUPERSET_ADMIN_USERNAME` / `_PASSWORD` | `admin` / `admin` | Superset sign-in |
