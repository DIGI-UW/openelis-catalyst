import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { DemoMilestones } from "./support/demo-milestones";
import { openComposer } from "./support/open-composer";
import { runSupersetImport } from "./support/superset-import";

/*
 * The whole path, from a FHIR server to a published Superset dashboard.
 *
 * The other demos open on a warehouse that already exists, which leaves the
 * most-asked question unanswered: where did this data come from? This one
 * starts a step earlier, at the FHIR Data Pipes control panel, and shows the
 * warehouse being built from a live FHIR endpoint before anything is asked of
 * it:
 *
 *   OpenMRS FHIR R4 -> Data Pipes microbatch -> Parquet + registered views
 *     -> Spark SQL -> Catalyst conversation -> Dataset -> Widgets
 *     -> Dashboard -> native bundle -> Superset
 *
 * Nothing here is a mock. The control panel is the pipeline's own UI, the
 * SQL is written by the configured model against the schema Spark reports,
 * and the number the dashboard shows in Superset is asserted to be the number
 * Catalyst returned -- read off the screen, not restated from a fixture.
 *
 * ONE spec, two modes -- the Playwright project picks it:
 *
 *   e2e (assertions, no video, no dwells):
 *     PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
 *       CATALYST_HARNESS_DIR=<running harness checkout> \
 *       npx playwright test e2e/fhir-to-dashboard-demo.spec.ts --project=deterministic
 *
 *   video (same steps, paced for camera):
 *     …same env… npx playwright test e2e/fhir-to-dashboard-demo.spec.ts --project=demo-video
 *
 * CATALYST_DEMO_PIPELINE_MODE picks how much of the pipeline act to run:
 *   INCREMENTAL (default) fetches what changed since the last run and merges
 *   it -- seconds, and the honest shape of a microbatch. FULL rebuilds the
 *   snapshot from every resource on the server, which is the same code path
 *   and takes as long as the source is large. Use FULL for a cut that has to
 *   show the warehouse being created from nothing.
 */

test.setTimeout(3_600_000);

const DATA_PIPES_URL =
  process.env.CATALYST_DATA_PIPES_URL ?? "http://127.0.0.1:18091";
const PIPELINE_MODE = (
  process.env.CATALYST_DEMO_PIPELINE_MODE ?? "INCREMENTAL"
).toUpperCase();
const PIPELINE_BUTTON: Record<string, string> = {
  INCREMENTAL: "Run Incremental",
  FULL: "Run Full",
  VIEWS: "Recreate Views",
};

const COUNT_DATASET_BASE = "HIV patients in the warehouse";
const GENDER_DATASET_BASE = "HIV patients by gender";
const COUNT_WIDGET_BASE = "Patients";
const GENDER_WIDGET_BASE = "Patients by gender";
const DASHBOARD_BASE = "OpenMRS HIV programme overview";

/** The query reads from a relation FHIR Data Pipes registered for this source.
 *
 * Anchored on FROM/JOIN rather than a bare name, because an unanchored name
 * would also accept the alias `patient_count` as if it were a relation.
 *
 * The writer picks which relation to use; the demo only asserts it picked one
 * of the warehouse's, because pinning the generated SQL to a single table
 * would make the model's freedom look like a scripted answer.
 */
const READS_A_DISCOVERED_RELATION =
  /\b(?:FROM|JOIN)\s+(?:\w+\.)?(?:patient_flat|observation_flat|encounter_flat|condition_flat|medication_flat|medication_request_flat|patient|observation|encounter|condition|medication|medicationrequest)\b/i;

test("FHIR endpoint to a published Superset dashboard", async ({ page }, info) => {
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack scenario; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );
  const runButton = PIPELINE_BUTTON[PIPELINE_MODE];
  if (!runButton) {
    throw new Error(
      `CATALYST_DEMO_PIPELINE_MODE must be one of ${Object.keys(PIPELINE_BUTTON).join(", ")}`,
    );
  }

  const filming = info.project.name === "demo-video";
  const timing = new DemoMilestones("fhir-to-dashboard-demo");
  const runId = process.env.CATALYST_DEMO_RUN_ID?.trim() || randomUUID();
  const runName = (base: string) => `${base} · ${runId}`;
  const countDataset = runName(COUNT_DATASET_BASE);
  const genderDataset = runName(GENDER_DATASET_BASE);
  const countWidget = runName(COUNT_WIDGET_BASE);
  const genderWidget = runName(GENDER_WIDGET_BASE);
  const dashboard = runName(DASHBOARD_BASE);

  /** Hold the frame so a viewer can read; nothing at all when testing. */
  const dwell = async (ms: number) => {
    if (filming) await page.waitForTimeout(ms);
  };
  /** The editor's SQL as one line.
   *
   * CodeMirror renders every line in its own element, so textContent runs the
   * lines together -- `patient_countFROM default.patient` -- and no pattern
   * anchored on word boundaries can match. innerText keeps the line breaks.
   */
  const expectSqlReadsTheWarehouse = async () => {
    await expect
      .poll(
        async () =>
          (
            await page.getByRole("textbox", { name: "SQL query" }).innerText()
          ).replace(/\s+/g, " "),
        {
          message: "generated SQL does not read a relation Spark reported",
          timeout: 30_000,
        },
      )
      .toMatch(READS_A_DISCOVERED_RELATION);
  };

  /** Type visibly on camera, instantly when testing. */
  const type = async (locator: ReturnType<typeof page.getByLabel>, text: string) => {
    if (filming) await locator.pressSequentially(text, { delay: 28 });
    else await locator.fill(text);
  };

  // ---- Act 1: the warehouse is built from a FHIR server -------------------
  // The pipeline's own control panel, not a Catalyst screen: this act exists
  // to show that the data Catalyst queries is produced by FHIR Data Pipes
  // reading a FHIR endpoint, and that the shipped Parquet warehouse is what
  // it writes.
  await page.goto(`${DATA_PIPES_URL}/`);
  await expect(
    page.getByText("FHIR Pipelines Control Panel", { exact: false }).first(),
  ).toBeVisible();
  timing.mark("control-panel-open");
  await dwell(2_500);

  // The configured source is a FHIR endpoint, on screen, in the pipeline's
  // own configuration table. The table is a collapsed accordion, so opening
  // it is the beat that shows where this data comes from.
  await page
    .getByRole("button", { name: "Main configuration parameters" })
    .click();
  await expect(page.getByText("fhirdata.fhirServerUrl").first()).toBeVisible();
  await expect(page.getByText(/\/ws\/fhir2\/R4/).first()).toBeVisible();
  timing.mark("fhir-endpoint-shown");
  await dwell(4_000);

  // The shipped warehouse is on: Parquet files, the ViewDefinition views
  // Spark serves, and where both are written. This is the whole difference
  // between this deployment and one that sinks its projections elsewhere.
  // (createHiveResourceTables is set for this pipeline but the panel does not
  // list it, so it is not asserted here -- the analytics contract test covers
  // all three.)
  for (const setting of [
    "fhirdata.generateParquetFiles",
    "fhirdata.createParquetViews",
    "fhirdata.viewDefinitionsDir",
    "fhirdata.dwhRootPrefix",
  ]) {
    await expect(page.getByText(setting).first()).toBeVisible();
  }
  timing.mark("warehouse-settings-shown");
  await dwell(4_000);

  // Run the pipeline. INCREMENTAL merges what changed since the last run --
  // the microbatch -- and FULL rebuilds the snapshot from the whole server.
  await expect(page.getByRole("button", { name: runButton })).toBeVisible();
  await dwell(1_500);
  await page.getByRole("button", { name: runButton }).click();
  timing.mark("pipeline-started");

  // Poll the controller's own status endpoint rather than the panel's banner:
  // the panel reloads itself on completion, and a reload mid-assertion is a
  // flake. This is the same status the panel reads.
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${DATA_PIPES_URL}/status`);
        if (!response.ok()) return "UNAVAILABLE";
        const body = (await response.json()) as { pipelineStatus?: string };
        return body.pipelineStatus ?? "UNKNOWN";
      },
      {
        message: `${PIPELINE_MODE} pipeline did not return to IDLE`,
        timeout: 3_000_000,
        intervals: [5_000],
      },
    )
    .not.toBe("RUNNING");
  timing.mark("pipeline-finished");

  // The snapshot the run wrote, named on screen. This is the Parquet the
  // Spark thriftserver serves.
  await page.goto(`${DATA_PIPES_URL}/`);
  await expect(page.getByText(/\/dwh\/.*_DWH_TIMESTAMP_/).first()).toBeVisible();
  timing.mark("snapshot-shown");
  await dwell(5_000);

  // ---- Act 2: ask the warehouse a question in plain language --------------
  await page.goto("/?dataSource=openmrs-hiv");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  await expect(
    page.getByText("OpenMRS HIV/ART program", { exact: true }).first(),
  ).toBeVisible();
  timing.mark("catalyst-open");
  await dwell(2_500);

  await expect(page.getByLabel("Model profile")).toBeEnabled();
  await page
    .getByLabel("Model profile")
    .selectOption("catalyst-query-gemma-4-12b-qwen2.5-14b-checked");
  await type(
    page.getByLabel("Question"),
    "How many patients are in this dataset?",
  );
  timing.mark("question-typed");
  await dwell(1_200);
  await page.getByRole("button", { name: "Generate query" }).click();
  timing.mark("generate-clicked");

  await expect(page.getByRole("heading", { name: /^Refine \[1\]$/ })).toBeVisible({
    timeout: 600_000,
  });
  // The SQL names a relation Spark reported, which is the point: the schema
  // came from the warehouse the previous act built, not from a curated list.
  await expectSqlReadsTheWarehouse();
  timing.mark("sql-ready-1");
  await dwell(6_000);

  await page.getByRole("button", { name: "Run query" }).click();
  const countResult = page.locator(".query-turn__dataset").first();
  await expect(countResult).toBeVisible({ timeout: 300_000 });
  const countCell = countResult.locator("tbody tr").first().getByRole("cell").first();
  await expect(countCell).toBeVisible();
  // Read the answer off the screen. Everything downstream is asserted against
  // this, so the dashboard is checked against what Catalyst actually returned
  // rather than against a number written into the test.
  const catalystCount = ((await countCell.textContent()) ?? "").trim();
  expect(catalystCount).toMatch(/^\d[\d,]*$/);
  timing.mark("result-1");
  await dwell(5_000);

  /** Save the current turn's dataset draft under a real name. */
  const saveDataset = async (name: string) => {
    await page.getByRole("button", { name: "Save to datasets" }).click();
    const nameBox = page.getByPlaceholder(/Dataset from Query v/);
    await expect(nameBox).toBeVisible();
    await nameBox.click();
    await type(nameBox, name);
    await page.getByRole("button", { name: "Save Dataset" }).click();
    await expect(page.getByRole("button", { name: "Saving…" })).toHaveCount(0);
    await dwell(1_500);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Close" })
      .first()
      .click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  };

  await saveDataset(countDataset);
  timing.mark("dataset-saved-1");

  // ---- Act 3: refine it in conversation -----------------------------------
  await openComposer(page);
  await type(
    page.getByRole("textbox", { name: "Follow-up instruction" }),
    "Now break that count down by gender, highest first",
  );
  timing.mark("followup-typed");
  await dwell(1_200);
  await page.getByRole("button", { name: "Generate next query" }).click();
  timing.mark("generate-clicked-2");

  await expect(page.getByRole("heading", { name: /^Refine \[2\]$/ })).toBeVisible({
    timeout: 600_000,
  });
  await expectSqlReadsTheWarehouse();
  timing.mark("sql-ready-2");
  await dwell(6_000);

  await page.getByRole("button", { name: "Run query" }).click();
  const genderResult = page.locator(".query-turn__dataset").last();
  await expect(genderResult).toBeVisible({ timeout: 300_000 });
  await expect(genderResult.locator("tbody tr").first()).toBeVisible();
  timing.mark("result-2");
  await dwell(5_000);
  await saveDataset(genderDataset);
  timing.mark("dataset-saved-2");

  // ---- Act 4: widgets over the saved datasets -----------------------------
  /** Build one widget over a saved dataset. */
  const saveWidget = async (name: string, dataset: string, visualization: string) => {
    await page.getByRole("button", { name: "New Widget" }).click();
    await type(page.getByRole("textbox", { name: "Widget name" }), name);
    await page
      .getByRole("combobox", { name: "Reads Dataset" })
      .selectOption({ label: dataset });
    await page
      .getByRole("combobox", { name: "Visualization" })
      .selectOption({ label: visualization });
    await dwell(2_000);
    await page.getByRole("button", { name: "Save Widget" }).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await dwell(1_500);
  };

  await page.getByRole("button", { name: "Widgets" }).click();
  await dwell(1_500);
  await saveWidget(countWidget, countDataset, "Big number");
  timing.mark("widget-count");
  await saveWidget(genderWidget, genderDataset, "Grouped bar");
  timing.mark("widget-gender");

  // ---- Act 5: the dashboard, published as a native bundle -----------------
  await page.getByRole("button", { name: "Dashboards" }).click();
  await dwell(1_200);
  await page.getByRole("button", { name: "New Dashboard" }).click();
  await type(page.getByRole("textbox", { name: "Dashboard name" }), dashboard);
  await page.getByRole("checkbox", { name: countWidget, exact: true }).check();
  await page.getByRole("checkbox", { name: genderWidget, exact: true }).check();
  await dwell(1_500);
  const savedDashboardResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        "/v1/catalyst/dashboard-builder/dashboards",
  );
  await page.getByRole("button", { name: "Save Dashboard" }).click();
  const savedDashboard = (await (await savedDashboardResponse).json()) as {
    versionId?: unknown;
  };
  if (typeof savedDashboard.versionId !== "string") {
    throw new Error("saved Dashboard response did not include its version ID");
  }

  const card = page.locator("article").filter({ hasText: dashboard });
  await expect(card).toBeVisible({ timeout: 60_000 });
  const publishedDashboardResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/v1/catalyst/dashboard-builder/dashboards/${encodeURIComponent(savedDashboard.versionId as string)}/publish`,
  );
  await card.getByRole("button", { name: "Publish to Superset" }).click();
  const publication = (await (await publishedDashboardResponse).json()) as {
    pointer?: { bundle?: { sha256?: unknown } };
  };
  const bundleDigest = publication.pointer?.bundle?.sha256;
  if (typeof bundleDigest !== "string" || !/^[a-f0-9]{64}$/.test(bundleDigest)) {
    throw new Error("published Dashboard response did not include a bundle digest");
  }
  await expect(card.getByText("Superset bundle ready")).toBeVisible({
    timeout: 60_000,
  });
  timing.mark("bundle-ready");
  await dwell(4_000);

  // ---- Act 6: the seam -- the pinned importer -----------------------------
  timing.mark("import-started");
  runSupersetImport(bundleDigest);
  timing.mark("imported");
  await page.reload();
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Dashboards" }).click();
  await expect(card.getByText("Imported", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  timing.mark("imported-visible");
  await dwell(4_000);

  // ---- Act 7: the same number, in Superset --------------------------------
  const openLink = card.getByRole("link", { name: "Open Superset" });
  await expect(openLink).toBeVisible();
  const href = await openLink.getAttribute("href");
  if (!href) throw new Error("Open Superset link has no href");
  const supersetBase =
    process.env.PLAYWRIGHT_SUPERSET_URL ?? "http://127.0.0.1:18088";
  const dashboardUrl = new URL(new URL(href).pathname, supersetBase).toString();

  await page.goto(`${supersetBase}/login/`);
  await page.locator("#username").fill(process.env.SUPERSET_ADMIN_USERNAME ?? "admin");
  await page.locator("#password").fill(process.env.SUPERSET_ADMIN_PASSWORD ?? "admin");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForLoadState("networkidle");
  await page.goto(dashboardUrl);
  timing.mark("superset-open");

  await expect(page.getByText(dashboard, { exact: false }).first()).toBeVisible({
    timeout: 120_000,
  });
  // The count Catalyst returned, rendered by Superset out of the same Spark
  // source. Superset formats large numbers its own way, so compare on digits.
  const digits = catalystCount.replace(/,/g, "");
  await expect(
    page.getByText(new RegExp(`\\b${digits.replace(/(\d)(?=(\d{3})+$)/g, "$1[,\\\\s]?")}\\b`)).first(),
  ).toBeVisible({ timeout: 120_000 });
  await expect(page.locator("canvas").first()).toBeVisible({ timeout: 120_000 });
  timing.mark("dashboard-rendered");
  await dwell(9_000);
  timing.mark("end");
  timing.save();
});
