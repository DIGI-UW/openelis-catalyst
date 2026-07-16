import { expect, test, type Page } from "@playwright/test";
import {
  executionOutcome,
  preview,
  QUESTION,
  table,
} from "../src/features/query/test/fixtures";

const query = process.env.PLAYWRIGHT_QUERY ?? QUESTION;

const installDeterministicApi = async (page: Page) => {
  await page.route("**/v1/catalyst/queries", async (route) => {
    const request = route.request();
    expect(request.method()).toBe("POST");
    expect(request.postDataJSON()).toMatchObject({
      contractVersion: "catalyst.question.request.v1",
      deploymentMode: "demo",
      question: query,
    });
    await route.fulfill({ status: 201, json: { ...preview, question: query } });
  });

  await page.route("**/v1/catalyst/previews/*/execute", async (route) => {
    const request = route.request();
    expect(request.method()).toBe("POST");
    expect(request.postDataJSON()).toMatchObject({
      contractVersion: "catalyst.execute.request.v1",
      previewId: preview.previewId,
      queryDigest: preview.queryDigest,
      accept: true,
    });
    await route.fulfill({
      status: 202,
      json: executionOutcome("in_progress"),
    });
  });

  await page.route("**/v1/catalyst/executions/*?*", async (route) => {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({
      status: 200,
      json: { ...table, question: query },
    });
  });
};

test("question to accepted preview to typed table", async ({
  page,
}, testInfo) => {
  const useMockApi =
    testInfo.project.name === "deterministic" ||
    process.env.PLAYWRIGHT_USE_MOCK_API !== "false";

  if (useMockApi) {
    await installDeterministicApi(page);
  }

  await page.goto("/");

  await expect(page.getByText("Demo environment", { exact: true })).toBeVisible();
  await page.getByLabel("Question").fill(query);
  await page.getByRole("button", { name: "Generate preview" }).click();

  await expect(page.getByRole("heading", { name: "Review query" })).toBeVisible();
  await expect(page.getByLabel("Generated SQL")).toBeVisible();
  await expect(page.getByText("minimum_result", { exact: true })).toBeVisible();
  await expect(page.getByText("integer", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Accept and run" }).click();

  const results = page.getByRole("region", { name: "Query results" });
  await expect(results).toBeVisible();
  await expect(results.getByText("1200", { exact: true })).toBeVisible();
  await expect(results.getByText("450", { exact: true })).toBeVisible();
  await expect(results.getByText("80", { exact: true })).toBeVisible();

  const provenance = page.getByRole("region", { name: "Provenance" });
  await expect(provenance).toBeVisible();
  await expect(
    provenance.getByText("catalyst-query-checked", { exact: true }),
  ).toBeVisible();
  await expect(
    provenance.getByText("cat-trace-123", { exact: true }),
  ).toBeVisible();
  await expect(
    provenance.getByText("hub-trace-456", { exact: true }),
  ).toBeVisible();

  await expect(page.getByText("Demo environment", { exact: true })).toBeVisible();
});
