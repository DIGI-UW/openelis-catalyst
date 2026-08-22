import { expect, test } from "@playwright/test";
import { DemoMilestones } from "./support/demo-milestones";
import { openComposer } from "./support/open-composer";

// Live demo-quality recording: a two-turn analytical conversation against the
// OpenMRS HIV/ART data source alone (selected before the first question, no
// mid-session switch). Turn 1 asks a bounded question; turn 2 adds patient
// demographic detail, showing iterative refinement from the exact current
// query. This is a manual/demo verification script, not CI coverage: it
// skips unless PLAYWRIGHT_LIVE=true, requires the live stack up, and is not
// run by the default test suite. Run with:
//   PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
//     npx playwright test e2e/openmrs-hiv-demo.spec.ts --project=demo-video
test.setTimeout(1_200_000);

test("OpenMRS HIV/ART: ask, then add patient demographic detail", async ({
  page,
}) => {
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack demo; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );

  const timing = new DemoMilestones("openmrs-hiv-demo");
  // Open straight onto the HIV/ART catalog via the shareable ?dataSource
  // link, so the whole conversation targets it from the first turn.
  //
  // The in-app picker moved into the new-session menu during the workbench
  // rework, and that menu also lists every recent session on the machine --
  // fine in the product, wrong on camera for a published cut, where it would
  // put local scratch sessions on the project's homepage. The deep link is
  // the same selection without the bystanders, and it mirrors how the
  // OpenELIS cut opens.
  await page.goto("/?dataSource=openmrs-hiv");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  await expect(
    page.getByText("OpenMRS HIV/ART program", { exact: true }).first(),
  ).toBeVisible();
  timing.mark("source-selected");

  // Turn 1: a bounded question against the HIV program data.
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  // The HIV catalog carries both the curated hiv_observation_fact_v1 view and
  // the lossless observation_flat base it is built over. The default E4B
  // writer reliably confuses the two -- it selects FROM observation_flat while
  // reaching for the view's column names (value_numeric, concept_name,
  // patient_gender), which parses, passes review, and then fails in Postgres.
  // Measured over four generations: E4B produced invalid SQL every time; the
  // 12B writer picked the curated view with correct columns and returned 100
  // rows. The profile is named on screen throughout the recording, so the cut
  // shows exactly which models produced what.
  await page
    .getByLabel("Model profile")
    .selectOption("catalyst-query-gemma-4-12b-qwen2.5-14b-checked");
  // Typed rather than filled: fill() sets the value in one frame, so the cut
  // opens on a question that was simply always there. Typing it is the beat
  // that establishes this is a plain-language question, not a canned query.
  await page
    .getByLabel("Question")
    .pressSequentially(
      "Show CD4 count results since 2026-01-01 with patient, value, and observed date",
      { delay: 28 },
    );
  timing.mark("question-typed");
  await page.waitForTimeout(1_200);
  await page.getByRole("button", { name: "Generate query" }).click();
  timing.mark("generate-clicked");

  await expect(
    page.getByRole("heading", { name: /^Refine \[1\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  // Deliberately strict. The HIV catalog exposes both the curated
  // hiv_observation_fact_v1 view and the lossless observation_flat base it is
  // built over, and those two have different column vocabularies
  // (value_numeric/observed_at vs val_quantity/obs_date). Accepting either
  // relation here once let through SQL that selected value_numeric FROM
  // observation_flat -- columns from one relation against the other -- which
  // parses, reaches the database, and fails there. Pinning the curated view
  // keeps this a demo of a query that runs.
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "hiv_observation_fact_v1",
  );
  timing.mark("sql-ready-1");
  // Dwell on the drafted SQL. Without this the capture cuts from "generating"
  // to "results" with no frame in between where the query is actually
  // readable -- and readable SQL is the whole claim being demonstrated.
  await page.waitForTimeout(5_000);
  timing.mark("sql-read-1");

  await page.getByRole("button", { name: "Run query" }).click();
  // The run's result is that cell's dataset, in the cell.
  const dataset = page.locator(".query-turn__dataset").first();
  await expect(dataset).toBeVisible({ timeout: 120_000 });
  await expect(dataset.getByText(/^Dataset from \[\d+\]$/)).toBeVisible({
    timeout: 120_000,
  });
  timing.mark("dataset-1");
  // Local Postgres answers in ~100ms, so without a dwell the first result
  // table is on screen for a single frame before turn 2 starts typing.
  await page.waitForTimeout(5_000);
  timing.mark("dataset-read-1");

  // Turn 2: add demographic detail from the exact current query — an
  // iterative refinement, not a fresh question.
  await openComposer(page);
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .pressSequentially("Also include the patient's gender and birth date", {
      delay: 28,
    });
  timing.mark("followup-typed");
  await page.waitForTimeout(1_200);
  await page.getByRole("button", { name: "Generate next query" }).click();
  timing.mark("generate-clicked-2");

  await expect(
    page.getByRole("heading", { name: /^Refine \[2\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "hiv_observation_fact_v1",
  );
  timing.mark("sql-ready-2");
  await page.waitForTimeout(5_000);
  timing.mark("sql-read-2");

  await page.getByRole("button", { name: "Run query" }).click();
  await expect(page.locator(".query-turn__dataset").last()).toBeVisible({
    timeout: 120_000,
  });
  timing.mark("dataset-2");
  // Let the final table sit on screen so the cut has a real tail to work with.
  await page.waitForTimeout(6_000);
  timing.mark("end");
  timing.save();
});
