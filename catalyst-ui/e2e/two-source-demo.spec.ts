import { expect, test } from "@playwright/test";

// Live end-to-end demo across BOTH data sources: an OpenELIS laboratory
// question generates and executes, then the data source is switched
// mid-session to the OpenMRS HIV/ART program and a follow-up adapts the
// query to that source's schema — proving per-turn source targeting,
// per-source catalogs, and execution routing against the real stack
// (gateway, Med-Agent Hub, real writer/reviewer models, both analytics
// databases). Run with:
//   PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
//     npx playwright test e2e/two-source-demo.spec.ts --project=demo-video
test.setTimeout(1_200_000);

test("two data sources: generate on OpenELIS, switch and adapt to OpenMRS HIV", async ({
  page,
}) => {
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack demo; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );

  await page.goto("/");

  // The page is Catalyst with both registered data sources selectable.
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  const sourcePicker = page.getByRole("combobox", { name: "Data source" });
  await expect(sourcePicker).toBeVisible();
  await expect(
    sourcePicker.getByRole("option", { name: "OpenELIS Laboratory" }),
  ).toHaveCount(1);
  await expect(
    sourcePicker.getByRole("option", { name: "OpenMRS HIV/ART program" }),
  ).toHaveCount(1);
  await expect(sourcePicker).toHaveValue("openelis");

  // Turn 1: a laboratory question against the OpenELIS source.
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

  // Execute against the OpenELIS analytics database.
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

  // Switch the data source mid-session and adapt the query to it.
  await sourcePicker.selectOption("openmrs-hiv");
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill(
      "Adapt this query to the HIV program data: show CD4 count results since 2026-01-01 with patient, value, and observed date",
    );
  await page.getByRole("button", { name: "Generate next query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine Query v2$/ }),
  ).toBeVisible({ timeout: 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    "hiv_observation_fact_v1",
  );

  // The turn timeline attributes each turn to its data source.
  await expect(
    page.locator(".query-turn__source", { hasText: "OpenELIS Laboratory" }),
  ).toBeVisible();
  await expect(
    page.locator(".query-turn__source", { hasText: "OpenMRS HIV/ART program" }),
  ).toBeVisible();

  // Execute against the OpenMRS HIV analytics database.
  await page.getByRole("button", { name: "Validate query" }).click();
  await page.getByRole("button", { name: "Run query" }).click();
  await expect(
    execution.getByRole("heading", { name: /Results from Query v2/ }),
  ).toBeVisible({ timeout: 120_000 });
});
