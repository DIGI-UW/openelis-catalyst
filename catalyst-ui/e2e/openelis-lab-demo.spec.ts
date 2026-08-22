import { expect, test } from "@playwright/test";
import { DemoMilestones } from "./support/demo-milestones";
import { openComposer } from "./support/open-composer";

// Live demo-quality recording: a two-turn analytical conversation against the
// OpenELIS laboratory data source alone (no data-source switch). Turn 1 asks
// a bounded question; turn 2 adds operationally useful detail (specimen
// turnaround time), showing iterative refinement from the exact current
// query rather than starting over. This is a manual/demo verification
// script, not CI coverage:
// it skips unless PLAYWRIGHT_LIVE=true, requires the live stack up, and is
// not run by the default test suite. Run with:
//   PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
//     npx playwright test e2e/openelis-lab-demo.spec.ts --project=demo-video
test.setTimeout(1_200_000);

test("OpenELIS laboratory: ask, then add specimen turnaround detail", async ({
  page,
}) => {
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack demo; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );

  const timing = new DemoMilestones("openelis-lab-demo");
  await page.goto("/");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  timing.mark("app-ready");

  // Turn 1: a bounded laboratory question against OpenELIS.
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  // Both published cuts run the 12B writer with the Qwen 2.5 14B reviewer, so
  // the two demos are comparable and neither is showing the smallest profile
  // the stack happens to default to. The profile is named on screen
  // throughout, so the recording discloses which models produced the query.
  await page
    .getByLabel("Model profile")
    .selectOption("catalyst-query-gemma-4-12b-qwen2.5-14b-checked");
  // Typed rather than filled: fill() sets the value in one frame, so the cut
  // opens on a question that was simply always there. Typing it is the beat
  // that establishes this is a plain-language question, not a canned query.
  await page
    .getByLabel("Question")
    .pressSequentially(
      "Show viral load results since 2026-01-01 with patient, value, and observed date",
      { delay: 28 },
    );
  timing.mark("question-typed");
  await page.waitForTimeout(1_200);
  await page.getByRole("button", { name: "Generate query" }).click();
  timing.mark("generate-clicked");

  await expect(
    page.getByRole("heading", { name: /^Refine \[1\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
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

  // Turn 2: add operationally useful detail from the exact current query —
  // an iterative refinement, not a fresh question.
  await openComposer(page);
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .pressSequentially(
      "Also include the result unit and how many minutes elapsed between specimen receipt and result release",
      { delay: 28 },
    );
  timing.mark("followup-typed");
  await page.waitForTimeout(1_200);
  await page.getByRole("button", { name: "Generate next query" }).click();
  timing.mark("generate-clicked-2");

  await expect(
    page.getByRole("heading", { name: /^Refine \[2\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
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
