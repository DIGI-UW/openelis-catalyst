import { expect, test } from "@playwright/test";

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

  await page.goto("/");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();

  // Turn 1: a bounded laboratory question against OpenELIS.
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  await page
    .getByLabel("Question")
    .fill(
      "Show viral load results since 2026-01-01 with patient, value, and observed date",
    );
  await page.getByRole("button", { name: "Generate query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine Query v1$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
  );

  await page.getByRole("button", { name: "Validate query" }).click();
  await expect(page.getByRole("heading", { name: "Validation" })).toBeVisible({
    timeout: 60_000,
  });
  await page.getByRole("button", { name: "Run query" }).click();
  const execution = page.getByRole("region", { name: "Latest execution" });
  await expect(execution).toBeVisible({ timeout: 120_000 });
  await expect(
    execution.getByRole("heading", { name: /Results from Query v1/ }),
  ).toBeVisible({ timeout: 120_000 });

  // Turn 2: add operationally useful detail from the exact current query —
  // an iterative refinement, not a fresh question.
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill(
      "Also include the result unit and how many minutes elapsed between specimen receipt and result release",
    );
  await page.getByRole("button", { name: "Generate next query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine Query v2$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "lab_result_fact_v1",
  );

  await page.getByRole("button", { name: "Validate query" }).click();
  await page.getByRole("button", { name: "Run query" }).click();
  await expect(
    execution.getByRole("heading", { name: /Results from Query v2/ }),
  ).toBeVisible({ timeout: 120_000 });
});
