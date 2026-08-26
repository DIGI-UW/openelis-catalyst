import { expect, test, type Page } from "@playwright/test";

// Three real-product journeys, run against a live stack with the profile named
// for this run. These verify user-visible behavior, not model scores or a team
// selection. Run with:
//   PLAYWRIGHT_LIVE=true PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
//   PHASE1_PROFILE=<profile id to exercise> \
//     npx playwright test e2e/phase1-journeys.spec.ts
test.setTimeout(1_200_000);

const PROFILE = process.env.PHASE1_PROFILE ?? "catalyst-query-gemma-4-12b";
const live = () =>
  test.skip(
    process.env.PLAYWRIGHT_LIVE !== "true",
    "Live-stack journey; set PLAYWRIGHT_LIVE=true with PLAYWRIGHT_BASE_URL.",
  );

async function openHivSession(page: Page) {
  await page.goto("/?dataSource=openmrs-hiv");
  await expect(page.getByText("Catalyst", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  await page.getByLabel("Model profile").selectOption(PROFILE);
}

async function ask(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByRole("button", { name: "Generate query" }).click();
}

test("journey 1: patient names -> ready -> validate -> execute -> table", async ({
  page,
}) => {
  live();
  await openHivSession(page);
  await ask(
    page,
    "List CD4 count results since 2026-02-01 with the patient's family name, the value, the unit, and the observed date.",
  );
  // The writer produces a ready query naming real patients.
  await expect(page.locator(".sql-editor, textarea, pre").first()).toBeVisible({
    timeout: 300_000,
  });
  await page.getByRole("button", { name: /^Run( query)?$/ }).click();
  const table = page.locator("table").first();
  await expect(table).toBeVisible({ timeout: 120_000 });
  // The table carries the name column Phase 1 exists to provide.
  await expect(table).toContainText(/family/i);
});

test("journey 2: ambiguous ask -> clarification -> frozen answer -> ready; refresh restores", async ({
  page,
}) => {
  live();
  await openHivSession(page);
  await ask(page, "Show recent HIV results.");
  // The writer (or the deterministic preflight) asks instead of failing.
  await expect(
    page.getByText(/did you mean|which/i).first(),
  ).toBeVisible({ timeout: 300_000 });

  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill(
      "The last 90 days, and only CD4 count, CD4 percentage, and HIV viral load.",
    );
  await page.getByRole("button", { name: "Generate next query" }).click();
  await expect(page.getByText(/SELECT/i).first()).toBeVisible({
    timeout: 300_000,
  });

  // A reload restores the complete timeline and the selected version.
  await page.reload();
  await expect(page.getByText("Show recent HIV results.")).toBeVisible();
  await expect(page.getByText(/SELECT/i).first()).toBeVisible();
});

test("journey 3: conversation instructions survive reload; addresses are unsupported", async ({
  page,
}) => {
  live();
  await openHivSession(page);
  await ask(
    page,
    "Count medication requests by medication name, excluding do_not_perform requests.",
  );
  await expect(page.getByText(/SELECT/i).first()).toBeVisible({
    timeout: 300_000,
  });

  // Reload, then continue the same visible conversation. The opening user's
  // exclusion remains part of the session history without a hidden control.
  await page.reload();
  await expect(
    page.getByText(/excluding do_not_perform requests/i),
  ).toBeVisible();

  // The later regroup must honor that earlier instruction without repeating it.
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill("Regroup that by patient gender as well as medication name.");
  await page.getByRole("button", { name: "Generate next query" }).click();
  await expect(page.getByText(/do_not_perform/i).first()).toBeVisible({
    timeout: 300_000,
  });

  // And an unanswerable request declines with no SQL, keeping the previous
  // selected version in place.
  await page
    .getByRole("textbox", { name: "Follow-up instruction" })
    .fill("Now show each patient's home address.");
  await page.getByRole("button", { name: "Generate next query" }).click();
  await expect(
    page.getByText(/does not (contain|record)|unsupported|no .*address/i).first(),
  ).toBeVisible({ timeout: 300_000 });
  await expect(page.getByText(/do_not_perform/i).first()).toBeVisible();
});
