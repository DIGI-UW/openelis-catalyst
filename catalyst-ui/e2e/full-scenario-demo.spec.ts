import { expect, test } from "@playwright/test";
import { DemoMilestones } from "./support/demo-milestones";
import { openComposer } from "./support/open-composer";
import { runSupersetImport } from "./support/superset-import";

/*
 * The full scenario, end to end, through the product's own dashboard path:
 * a plain-language laboratory question becomes checked SQL in the Catalyst
 * workbench, is refined in conversation, both results are saved as governed
 * Datasets, a table Widget and a grouped-bar Widget are built over them, a
 * Dashboard collects both and is published as a native Superset bundle, the
 * pinned importer brings it in, and the finished dashboard renders in
 * Superset — a table and a graph, from two English sentences.
 *
 * (An earlier draft of this demo hand-carried the SQL into Superset SQL Lab
 * and rebuilt the charts there by hand; see the demo issue log for the 20+
 * Superset quirks that cost. This is the path the product actually ships.)
 *
 * ONE spec, two modes — the same run either way; the Playwright project
 * picks it:
 *
 *   e2e (assertions, no video, no dwells):
 *     PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
 *       CATALYST_STACK_DIR=<running stack checkout> \
 *       npx playwright test e2e/full-scenario-demo.spec.ts --project=deterministic
 *
 *   video (same steps, paced for camera):
 *     …same env… npx playwright test e2e/full-scenario-demo.spec.ts --project=demo-video
 *
 * Recording is not a different script with different steps — a demo that
 * diverges from the test stops being evidence that the product works. The
 * only difference is `dwell()`, a no-op outside the video project.
 */

test.setTimeout(1_800_000);

const DETAIL_DATASET = "Viral load results since Jan 2026";
const VOLUME_DATASET = "Result volumes by test";
const TABLE_WIDGET = "Recent viral load results";
const BAR_WIDGET = "Volumes by test";
const DASHBOARD = "Laboratory results overview";

test("plain-language question to a published Superset dashboard", async ({
  page,
}, info) => {
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack scenario; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );

  const filming = info.project.name === "demo-video";
  const timing = new DemoMilestones("full-scenario-demo");
  /** Hold the frame so a viewer can read; nothing at all when testing. */
  const dwell = async (ms: number) => {
    if (filming) await page.waitForTimeout(ms);
  };
  /** Type visibly on camera, instantly when testing. */
  const type = async (
    locator: ReturnType<typeof page.getByLabel>,
    text: string,
  ) => {
    if (filming) await locator.pressSequentially(text, { delay: 28 });
    else await locator.fill(text);
  };

  /** Save the current turn's dataset draft under a real name.
   *
   * The cell's "Save to datasets" opens the dataset review panel; only the
   * current turn offers it, so the locator is unique by construction. */
  const saveDataset = async (name: string) => {
    await page.getByRole("button", { name: "Save to datasets" }).click();
    const nameBox = page.getByPlaceholder(/Dataset from Query v/);
    await expect(nameBox).toBeVisible();
    await nameBox.click();
    await type(nameBox, name);
    await page.getByRole("button", { name: "Save Dataset" }).click();
    // Saving swaps the draft chrome for the saved entity; wait for the busy
    // label to clear before moving on.
    await expect(page.getByRole("button", { name: "Saving…" })).toHaveCount(0);
    await dwell(1_500);
    const close = page.getByRole("button", { name: "Close review panel" });
    if (await close.count()) await close.click();
  };

  /** Build one widget over a saved dataset. */
  const saveWidget = async (
    name: string,
    dataset: string,
    visualization: string,
  ) => {
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

  // ---- Act 1: the question ------------------------------------------------
  await page.goto("/?dataSource=openelis");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  await expect(
    page.getByText("OpenELIS Laboratory", { exact: true }).first(),
  ).toBeVisible();
  timing.mark("source-selected");
  await dwell(2_500);

  await expect(page.getByLabel("Model profile")).toBeEnabled();
  // The reviewed profile: a 12B writer drafts, a 14B reviewer checks — the
  // same lineup the published validation runs use.
  await page
    .getByLabel("Model profile")
    .selectOption("catalyst-query-gemma-4-12b-qwen2.5-14b-checked");
  await type(
    page.getByLabel("Question"),
    "Show viral load results since 2026-01-01 with patient, value, and observed date",
  );
  timing.mark("question-typed");
  await dwell(1_200);
  await page.getByRole("button", { name: "Generate query" }).click();
  timing.mark("generate-clicked");

  await expect(
    page.getByRole("heading", { name: /^Refine \[1\]$/ }),
  ).toBeVisible({ timeout: 600_000 });
  // Pinned deliberately: only the curated lab fact's column vocabulary
  // actually executes against the analytics database.
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
  );
  timing.mark("sql-ready-1");
  await dwell(6_000);

  await page.getByRole("button", { name: "Run query" }).click();
  await expect(page.locator(".query-turn__dataset").first()).toBeVisible({
    timeout: 120_000,
  });
  timing.mark("dataset-1");
  await dwell(5_000);
  await saveDataset(DETAIL_DATASET);
  timing.mark("dataset-saved-1");

  // ---- Act 2: refine in conversation ---------------------------------------
  await openComposer(page);
  await type(
    page.getByRole("textbox", { name: "Follow-up instruction" }),
    "Now count all results by test name instead, with the highest counts first",
  );
  timing.mark("followup-typed");
  await dwell(1_200);
  await page.getByRole("button", { name: "Generate next query" }).click();
  timing.mark("generate-clicked-2");

  await expect(
    page.getByRole("heading", { name: /^Refine \[2\]$/ }),
  ).toBeVisible({ timeout: 600_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
  );
  timing.mark("sql-ready-2");
  await dwell(6_000);

  await page.getByRole("button", { name: "Run query" }).click();
  await expect(page.locator(".query-turn__dataset").last()).toBeVisible({
    timeout: 120_000,
  });
  timing.mark("dataset-2");
  await dwell(5_000);
  await saveDataset(VOLUME_DATASET);
  timing.mark("dataset-saved-2");

  // ---- Act 3: two widgets over the governed datasets ----------------------
  await page.getByRole("button", { name: "Widgets" }).click();
  await dwell(1_500);
  await saveWidget(TABLE_WIDGET, DETAIL_DATASET, "Table");
  timing.mark("widget-table");
  await saveWidget(BAR_WIDGET, VOLUME_DATASET, "Grouped bar");
  timing.mark("widget-bar");

  // ---- Act 4: the dashboard -------------------------------------------------
  await page.getByRole("button", { name: "Dashboards" }).click();
  await dwell(1_200);
  await page.getByRole("button", { name: "New Dashboard" }).click();
  await type(page.getByRole("textbox", { name: "Dashboard name" }), DASHBOARD);
  await page.getByRole("checkbox", { name: TABLE_WIDGET }).check();
  await page.getByRole("checkbox", { name: BAR_WIDGET }).check();
  await dwell(1_500);
  await page.getByRole("button", { name: "Save Dashboard" }).click();

  const card = page.locator("article").filter({ hasText: DASHBOARD });
  await expect(card).toBeVisible({ timeout: 60_000 });
  // Saving may already mint the bundle; publish explicitly when it hasn't.
  if (!(await card.getByText("Superset bundle ready").count())) {
    await card.getByRole("button", { name: "Publish to Superset" }).click();
  }
  await expect(card.getByText("Superset bundle ready")).toBeVisible({
    timeout: 60_000,
  });
  timing.mark("bundle-ready");
  await dwell(4_000);

  // ---- Act 5: the seam — the pinned importer ------------------------------
  // The MVP has no Superset REST publication; a pinned CLI imports the
  // bundle and records a receipt, which is what flips the card to Imported.
  timing.mark("import-started");
  runSupersetImport();
  timing.mark("imported");
  // The card reads the receipt on navigation.
  await page.getByRole("button", { name: "Workbench" }).click();
  await page.getByRole("button", { name: "Dashboards" }).click();
  await expect(card.getByText("Imported", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  timing.mark("imported-visible");
  await dwell(4_000);

  // ---- Act 6: the finished dashboard in Superset --------------------------
  const openLink = card.getByRole("link", { name: "Open Superset" });
  await expect(openLink).toBeVisible();
  const href = await openLink.getAttribute("href");
  if (!href) throw new Error("Open Superset link has no href");
  const supersetBase =
    process.env.PLAYWRIGHT_SUPERSET_URL ?? "http://127.0.0.1:18088";
  const dashboardUrl = new URL(new URL(href).pathname, supersetBase).toString();

  // Sign in to Superset in the same page so the capture stays one video.
  await page.goto(`${supersetBase}/login/`);
  await page
    .locator("#username")
    .fill(process.env.SUPERSET_ADMIN_USERNAME ?? "admin");
  await page
    .locator("#password")
    .fill(process.env.SUPERSET_ADMIN_PASSWORD ?? "admin");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForLoadState("networkidle");
  await page.goto(dashboardUrl);
  timing.mark("superset-open");

  await expect(
    page.getByText(DASHBOARD, { exact: false }).first(),
  ).toBeVisible({ timeout: 120_000 });
  // The table widget shows real rows; the bar chart renders on canvas.
  await expect(page.getByText("Viral Load").first()).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.locator("canvas").first()).toBeVisible({
    timeout: 120_000,
  });
  timing.mark("dashboard-rendered");
  await dwell(9_000);
  timing.mark("end");
  timing.save();
});
