import { expect, test } from "@playwright/test";

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

  await page.goto("/");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();

  // Select the HIV/ART program data source before asking anything, so the
  // whole conversation targets it from the first turn.
  const sourcePicker = page.getByRole("combobox", { name: "Data source" });
  await expect(sourcePicker).toBeVisible();
  await sourcePicker.selectOption("openmrs-hiv");

  // Turn 1: a bounded question against the HIV program data.
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  await page
    .getByLabel("Question")
    .fill(
      "Show CD4 count results since 2026-01-01 with patient, value, and observed date",
    );
  await page.getByRole("button", { name: "Generate query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine \[1\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "hiv_observation_fact_v1",
  );

  await page.getByRole("button", { name: "Run query" }).click();
  // The run's result is that cell's dataset, in the cell.
  const dataset = page.locator(".query-turn__dataset").first();
  await expect(dataset).toBeVisible({ timeout: 120_000 });
  await expect(dataset.getByText(/^Dataset from \[\d+\]$/)).toBeVisible({
    timeout: 120_000,
  });

  // Turn 2: add demographic detail from the exact current query — an
  // iterative refinement, not a fresh question.
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill("Also include the patient's gender and birth date");
  await page.getByRole("button", { name: "Generate next query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine \[2\]$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "hiv_observation_fact_v1",
  );

  await page.getByRole("button", { name: "Run query" }).click();
  await expect(page.locator(".query-turn__dataset").last()).toBeVisible({
    timeout: 120_000,
  });
});
