import { expect, test, type Page } from "@playwright/test";
import type {
  BoundParameter,
  WorkbenchExecution,
  WorkbenchQueryVersion,
  WorkbenchSession,
  WorkbenchTurn,
  WorkbenchTurnTimeline,
  WorkbenchValidation,
} from "../src/features/query/types";
import { QUESTION } from "../src/features/query/test/fixtures";

const query = process.env.PLAYWRIGHT_QUERY ?? QUESTION;
const sessionId = "2bed91de-fa7d-4ffa-b4ae-0a454a883930";
const versionId = "d801dc1d-fc94-435b-bee6-2b45c3173af1";
const turnId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const queryDigest = "a".repeat(64);
const profileId = "catalyst-query-gemma-4-12b";
const sql = [
  "SELECT patient_id, result_value, observed_at",
  "FROM analytics.lab_result_fact_v1",
  "WHERE test_name = :test_name",
  "ORDER BY observed_at DESC",
  "LIMIT 2",
].join("\n");
const parameters: BoundParameter[] = [
  {
    name: "test_name",
    type: "string",
    source: "question",
    value: "Viral Load",
  },
];

const version: WorkbenchQueryVersion = {
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId,
  sessionId,
  parentVersionId: null,
  ordinal: 1,
  authorType: "model",
  sql,
  parameters,
  expectedColumns: [
    { name: "patient_id", logicalType: "string", nullable: false },
    { name: "result_value", logicalType: "decimal", nullable: true },
    { name: "observed_at", logicalType: "date-time", nullable: false },
  ],
  queryDigest,
  provenance: {
    model: "gemma-4-12b",
    collaborationRole: "writer",
    catalystTraceId: "cat-trace-notebook-123",
    hubTraceId: "hub-trace-notebook-456",
  },
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-07-18T00:00:01Z",
};

const validation: WorkbenchValidation = {
  contractVersion: "catalyst.workbench.validation.v1",
  queryDigest,
  validatorRevision: "catalyst.workbench.validator.v1",
  validatorDigest: "b".repeat(64),
  status: "valid",
  advisory: true,
  checks: [
    {
      name: "gateway_sql_policy",
      status: "passed",
      findingIds: [],
    },
  ],
  findings: [],
  durationMs: 3,
  validationId: "77777777-7777-4777-8777-777777777777",
  sessionId,
  versionId,
  ordinal: 1,
  createdAt: "2026-07-18T00:00:02Z",
};

const session = (
  validated: boolean,
): WorkbenchSession => ({
  contractVersion: "catalyst.workbench.session.v1",
  sessionId,
  question: query,
  profileId,
  datasetId: "openelis-fhir",
  datasetVersion: "pipeline-run-77",
  catalogVersion: "analytics-catalog-v1",
  currentVersionId: versionId,
  browserState: { sqlWrapLines: true },
  provenance: {
    catalystTraceId: "cat-trace-notebook-123",
    hubTraceId: "hub-trace-notebook-456",
    profileSnapshot: {
      profileId,
      profileLabel: "Catalyst query notebook",
      roleModels: {
        query_generate: "gemma-4-12b",
        query_review: "qwen2.5-14b",
      },
    },
  },
  status: "active",
  createdAt: "2026-07-18T00:00:00Z",
  updatedAt: validated
    ? "2026-07-18T00:00:02Z"
    : "2026-07-18T00:00:01Z",
  versions: [version],
  currentVersion: version,
  validations: validated ? [validation] : [],
  latestValidation: validated ? validation : null,
  executions: [],
});

const initialTurn: WorkbenchTurn = {
  contractVersion: "catalyst.workbench.turn.v1",
  sessionId,
  turnId,
  ordinal: 1,
  kind: "initial",
  origin: "recorded",
  instruction: query,
  instructionDigest: "c".repeat(64),
  profileSnapshot: {
    profileId,
    profileName: "Catalyst query notebook",
    profileDigest: "d".repeat(64),
    writer: { role: "writer", modelId: "gemma-4-12b" },
    reviewer: { role: "reviewer", modelId: "qwen2.5-14b" },
    omissions: [],
  },
  observedBase: null,
  editorSnapshot: null,
  snapshotClassification: "not_applicable",
  unresolvedPaths: [],
  effectiveBaseVersion: null,
  manualVersion: null,
  revisionContext: null,
  hubRequestDigest: "e".repeat(64),
  catalystTraceId: "cat-trace-notebook-123",
  hubTraceId: "hub-trace-notebook-456",
  generationEvidenceRef: {
    evidenceId: "99999999-9999-4999-8999-999999999999",
    evidenceDigest: "f".repeat(64),
    detailPath:
      `/v1/catalyst/workbench/sessions/${sessionId}/turns/${turnId}/generation-evidence`,
  },
  recoveryReferences: null,
  status: "completed",
  outputVersions: [
    {
      versionId,
      queryDigest,
      parentVersionId: null,
      role: "writer",
      authorType: "model",
      contractValid: true,
      validationId: null,
      selected: true,
    },
  ],
  selectedVersionId: versionId,
  resultingCurrentVersion: { versionId, queryDigest },
  events: [],
  failure: null,
  createdAt: "2026-07-18T00:00:00Z",
  updatedAt: "2026-07-18T00:00:01Z",
};

const timeline: WorkbenchTurnTimeline = {
  contractVersion: "catalyst.workbench.turn.timeline.v1",
  sessionId,
  currentTurnId: turnId,
  currentVersion: { versionId, queryDigest },
  turns: [initialTurn],
};

interface DeterministicApiCalls {
  sessionRequests: unknown[];
  versionRequests: unknown[];
  executionRequests: unknown[];
}

const installDeterministicApi = async (
  page: Page,
): Promise<DeterministicApiCalls> => {
  const calls: DeterministicApiCalls = {
    sessionRequests: [],
    versionRequests: [],
    executionRequests: [],
  };

  await page.route("**/v1/catalyst/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (method === "GET" && path === "/v1/catalyst/query-options") {
      await route.fulfill({
        status: 200,
        json: {
          contractVersion: "catalyst.query-options.v1",
          defaultProfileId: profileId,
          profiles: [
            {
              id: profileId,
              label: "Catalyst query notebook",
              available: true,
              revisionCapable: true,
              requiredModels: ["gemma-4-12b", "qwen2.5-14b"],
              roleModels: {
                query_generate: "gemma-4-12b",
                query_review: "qwen2.5-14b",
              },
              stages: ["query_generate", "query_lint", "query_review"],
              unavailableReasons: [],
            },
          ],
        },
      });
      return;
    }

    if (method === "GET" && path === "/v1/catalyst/dataset") {
      await route.fulfill({
        status: 200,
        json: {
          contractVersion: "catalyst.dataset-overview.v1",
          datasetId: "openelis-fhir",
          dataSource: "openelis-fhir-postgresql",
          pipelineRunId: "pipeline-run-77",
          synthetic: true,
          patients: 2,
          results: 2,
          testTypes: 1,
          firstObservedAt: "2026-07-01T00:00:00Z",
          lastObservedAt: "2026-07-02T00:00:00Z",
          tests: [
            {
              testName: "Viral Load",
              unit: "copies/ml",
              results: 2,
              patients: 2,
              minimum: "450",
              median: "825",
              maximum: "1200",
            },
          ],
          exampleQuestions: [],
        },
      });
      return;
    }

    if (method === "GET" && path === "/v1/catalyst/dataset/rows") {
      await route.fulfill({
        status: 200,
        json: {
          contractVersion: "catalyst.dataset-rows.v1",
          total: 2,
          limit: 25,
          offset: 0,
          rows: [],
        },
      });
      return;
    }

    if (method === "GET" && path === "/v1/catalyst/workbench/catalog") {
      await route.fulfill({
        status: 200,
        json: {
          contractVersion: "catalyst.workbench.editor-catalog.v1",
          catalogVersion: "analytics-catalog-v1",
          schemaVersion: "analytics-v1",
          dialect: "postgresql",
          schemas: [
            {
              name: "analytics",
              views: [
                {
                  name: "lab_result_fact_v1",
                  qualifiedName: "analytics.lab_result_fact_v1",
                  grain: "One row per laboratory result observation.",
                  columns: [
                    {
                      name: "patient_id",
                      logicalType: "string",
                      nullable: false,
                      description: "FHIR patient resource identifier.",
                    },
                    {
                      name: "result_value",
                      logicalType: "decimal",
                      nullable: true,
                      unitColumn: "result_unit",
                      description: "Numeric result value when available.",
                    },
                    {
                      name: "result_unit",
                      logicalType: "string",
                      nullable: true,
                      description: "Display unit for the numeric result.",
                    },
                    {
                      name: "observed_at",
                      logicalType: "date-time",
                      nullable: false,
                      description: "Observation effective time.",
                    },
                    {
                      name: "test_name",
                      logicalType: "string",
                      nullable: false,
                      description: "Display name of the laboratory test.",
                    },
                  ],
                },
              ],
            },
          ],
        },
      });
      return;
    }

    if (method === "POST" && path === "/v1/catalyst/workbench/sessions") {
      calls.sessionRequests.push(request.postDataJSON());
      await route.fulfill({ status: 201, json: session(false) });
      return;
    }

    if (
      method === "GET" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}/turns`
    ) {
      await route.fulfill({ status: 200, json: timeline });
      return;
    }

    if (
      method === "POST" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}/versions`
    ) {
      calls.versionRequests.push(request.postDataJSON());
      await route.fulfill({ status: 201, json: session(true) });
      return;
    }

    if (
      method === "POST" &&
      path === `/v1/catalyst/workbench/versions/${versionId}/execute`
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      calls.executionRequests.push(body);
      const execution: WorkbenchExecution = {
        contractVersion: "catalyst.workbench.execution.v1",
        queryDigest,
        idempotencyKey: String(body.idempotencyKey),
        validationStatus: "valid",
        query: { sql, parameters },
        statementTimeoutMs: 5000,
        maxRows: 100,
        replayed: false,
        status: "succeeded",
        result: {
          columns: [
            {
              ordinal: 1,
              name: "patient_id",
              databaseType: "text",
              typeOid: 25,
              logicalType: "string",
            },
            {
              ordinal: 2,
              name: "result_value",
              databaseType: "numeric",
              typeOid: 1700,
              logicalType: "decimal",
            },
            {
              ordinal: 3,
              name: "observed_at",
              databaseType: "timestamptz",
              typeOid: 1184,
              logicalType: "date-time",
            },
          ],
          rows: [
            [
              { type: "string", value: "patient-001" },
              { type: "decimal", value: "1200.0" },
              { type: "date-time", value: "2026-07-02T00:00:00Z" },
            ],
            [
              { type: "string", value: "patient-002" },
              { type: "decimal", value: "450.0" },
              { type: "date-time", value: "2026-07-01T00:00:00Z" },
            ],
          ],
          rowCount: {
            returned: 2,
            truncated: false,
            truncationReason: null,
          },
        },
        durationMs: 17,
        executionId: "88888888-8888-4888-8888-888888888888",
        sessionId,
        versionId,
        ordinal: 1,
        completedAt: "2026-07-18T00:00:03Z",
      };
      await route.fulfill({ status: 200, json: execution });
      return;
    }

    await route.fulfill({
      status: 500,
      json: { detail: `Unexpected mocked request: ${method} ${path}` },
    });
  });

  return calls;
};

test.setTimeout(480_000);

test("question to iterative notebook to validated typed results", async ({
  page,
}, testInfo) => {
  const useMockApi =
    testInfo.project.name === "deterministic" ||
    process.env.PLAYWRIGHT_USE_MOCK_API !== "false";
  const calls = useMockApi
    ? await installDeterministicApi(page)
    : null;

  if (useMockApi) {
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await page.goto("/");

  await expect(
    page.getByRole("complementary", { name: "Demo environment notice" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Supported query schema" }),
  ).toBeVisible();
  await expect(page.getByText("analytics.lab_result_fact_v1", { exact: true }))
    .toBeVisible();
  await page.getByRole("button", {
    name: /analytics\.lab_result_fact_v1.*columns/i,
  }).click();
  await expect(
    page.getByRole("cell", { name: "result_unit", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Model profile")).toBeEnabled();
  await page.getByLabel("Question").fill(query);
  await page.getByRole("button", { name: "Generate query" }).click();

  await expect(
    page.getByRole("heading", { name: /^Refine Query v1$/ }),
  ).toBeVisible({ timeout: useMockApi ? 5_000 : 420_000 });
  await expect(page.getByRole("textbox", { name: "SQL query" })).toContainText(
    useMockApi ? "analytics.lab_result_fact_v1" : "SELECT",
  );
  await expect(
    page.getByRole("region", { name: "Iterative query notebook" }),
  ).toBeVisible();
  await expect(page.getByLabel("Question")).toHaveCount(0);
  await expect(page.locator("textarea:enabled")).toHaveCount(1);
  await expect(
    page.getByRole("textbox", { name: "Follow-up instruction" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Minimize" }))
    .toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/has not been executed/i)).toBeVisible();

  if (useMockApi) {
    expect(calls?.sessionRequests).toEqual([
      {
        contractVersion: "catalyst.workbench.session.request.v1",
        deploymentMode: "demo",
        question: query,
        profileId,
      },
    ]);
    await expect(page.getByLabel("Parameter 1 name")).toHaveValue("test_name");
    await expect(page.getByLabel("Parameter 1 type")).toHaveValue("string");
    await expect(page.getByLabel("Parameter 1 value")).toHaveValue("Viral Load");
  }

  await page.getByRole("button", { name: "Validate query" }).click();
  if (useMockApi) {
    await expect(page.getByText("Valid", { exact: true })).toBeVisible();
    await expect.poll(() => calls?.versionRequests.length).toBe(1);
    expect(calls?.versionRequests[0]).toEqual({
      contractVersion: "catalyst.workbench.version.request.v1",
      parentVersionId: versionId,
      parentQueryDigest: queryDigest,
      sql,
      parameters,
      expectedColumns: version.expectedColumns,
    });
  } else {
    await expect(page.getByRole("heading", { name: "Validation" })).toBeVisible();
  }

  await page.getByRole("button", { name: "Run query" }).click();
  const execution = page.getByRole("region", { name: "Latest execution" });
  await expect(execution).toBeVisible();

  if (useMockApi) {
    await expect(
      execution.getByRole("heading", { name: "Results from Query v1" }),
    ).toBeVisible();
    await expect(execution.getByText("patient-001", { exact: true })).toBeVisible();
    await expect(execution.getByText("1200.0", { exact: true })).toBeVisible();
    await expect(execution.getByText("patient-002", { exact: true })).toBeVisible();
    await expect(execution.getByText("450.0", { exact: true })).toBeVisible();
    await expect(
      page.getByText(/Execution summary: Query v1 · Run 1 · 2 rows/i),
    ).toBeVisible();
    await expect(
      page.getByText(/Result row values are not included in model context/i),
    ).toBeVisible();
    await expect.poll(() => calls?.versionRequests.length).toBe(2);
    await expect.poll(() => calls?.executionRequests.length).toBe(1);
    expect(calls?.executionRequests[0]).toMatchObject({
      contractVersion: "catalyst.workbench.execute.request.v1",
      versionId,
      queryDigest,
      idempotencyKey: expect.any(String),
    });

    const provenance = page.getByRole("region", { name: "Run provenance" });
    await expect(provenance.getByText("gemma-4-12b", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText("qwen2.5-14b", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText("pipeline-run-77", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText(sessionId, { exact: true })).toBeVisible();
    await expect.poll(
      () => page.evaluate(() =>
        localStorage.getItem("catalyst.workbench.activeSessionId")),
    ).toBe(sessionId);
  } else {
    await expect(
      execution.getByRole("heading", {
        name: /^(Results from|Execution failed for) Query v\d+$/,
      }),
    ).toBeVisible();
  }

  await expect(
    page.getByRole("complementary", { name: "Demo environment notice" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Minimize" }).click();
  await expect(page.getByRole("button", { name: "Expand" }))
    .toHaveAttribute("aria-expanded", "false");
  await expect.poll(
    () => page.evaluate<boolean>(
      "document.documentElement.scrollWidth <= window.innerWidth",
    ),
  ).toBe(true);
});
