import { expect, test, type Locator, type Page } from "@playwright/test";
import type {
  BoundParameter,
  DashboardBuilderEntity,
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
const manualVersionId = "d801dc1d-fc94-435b-bee6-2b45c3173af2";
const successorVersionId = "d801dc1d-fc94-435b-bee6-2b45c3173af3";
const turnId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const followupTurnId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab";
const queryDigest = "a".repeat(64);
const manualQueryDigest = "1".repeat(64);
const successorQueryDigest = "2".repeat(64);
const profileId = "catalyst-query-e4b-qwen14b";
const revisionProfileId = "catalyst-query-e4b-qwen14b-alt";
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
const manualSql = sql.replace("LIMIT 2", "LIMIT 1");
const successorSql = manualSql.replace(
  "SELECT patient_id, result_value, observed_at",
  "SELECT patient_id, result_value, result_unit, observed_at",
);

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
    model: "google/gemma-4-e4b",
    collaborationRole: "writer",
    catalystTraceId: "cat-trace-notebook-123",
    hubTraceId: "hub-trace-notebook-456",
  },
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-07-18T00:00:01Z",
};

const manualVersion: WorkbenchQueryVersion = {
  ...version,
  versionId: manualVersionId,
  parentVersionId: versionId,
  ordinal: 2,
  authorType: "human",
  sql: manualSql,
  expectedColumns: [],
  queryDigest: manualQueryDigest,
  provenance: { editedFromVersionId: versionId },
  createdAt: "2026-07-18T00:00:03Z",
};

const successorVersion: WorkbenchQueryVersion = {
  ...version,
  versionId: successorVersionId,
  parentVersionId: manualVersionId,
  ordinal: 3,
  authorType: "model_repair",
  sql: successorSql,
  expectedColumns: [
    { name: "patient_id", logicalType: "string", nullable: false },
    { name: "result_value", logicalType: "decimal", nullable: true },
    { name: "result_unit", logicalType: "string", nullable: true },
    { name: "observed_at", logicalType: "date-time", nullable: false },
  ],
  queryDigest: successorQueryDigest,
  provenance: {
    profileId: revisionProfileId,
    profileLabel: "Gemma E4B writer + Qwen reviewer",
    roleModels: {
      query_generate: "google/gemma-4-e4b",
      query_review: "qwen2.5-14b-instruct-mlx",
    },
    model: "qwen2.5-14b-instruct-mlx",
    collaborationRole: "reviewer",
    turnId: followupTurnId,
  },
  createdAt: "2026-07-18T00:00:05Z",
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

const validationFor = (
  target: WorkbenchQueryVersion,
  ordinal: number,
): WorkbenchValidation => ({
  ...validation,
  validationId: `77777777-7777-4777-8777-${String(ordinal).padStart(12, "0")}`,
  queryDigest: target.queryDigest,
  versionId: target.versionId,
  ordinal,
  createdAt: `2026-07-18T00:00:0${ordinal + 2}Z`,
});

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
        query_generate: "google/gemma-4-e4b",
        query_review: "qwen2.5-14b-instruct-mlx",
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
    writer: { role: "writer", modelId: "google/gemma-4-e4b" },
    reviewer: { role: "reviewer", modelId: "qwen2.5-14b-instruct-mlx" },
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

const followupTurn: WorkbenchTurn = {
  ...initialTurn,
  turnId: followupTurnId,
  ordinal: 2,
  kind: "followup",
  instruction: "Include the result unit in the current query",
  instructionDigest: "3".repeat(64),
  profileSnapshot: {
    profileId: revisionProfileId,
    profileName: "Gemma E4B writer + Qwen reviewer",
    profileDigest: "4".repeat(64),
    writer: { role: "writer", modelId: "google/gemma-4-e4b" },
    reviewer: { role: "reviewer", modelId: "qwen2.5-14b-instruct-mlx" },
    omissions: [],
  },
  observedBase: {
    versionId: manualVersionId,
    queryDigest: manualQueryDigest,
  },
  editorSnapshot: {
    contractVersion: "catalyst.workbench.editor-snapshot-record.v1",
    editorDigest: manualQueryDigest,
  },
  snapshotClassification: "reused",
  effectiveBaseVersion: {
    versionId: manualVersionId,
    queryDigest: manualQueryDigest,
  },
  hubRequestDigest: "5".repeat(64),
  outputVersions: [
    {
      versionId: successorVersionId,
      queryDigest: successorQueryDigest,
      parentVersionId: manualVersionId,
      role: "reviewer",
      authorType: "model_repair",
      contractValid: true,
      validationId: validationFor(successorVersion, 2).validationId,
      selected: true,
    },
  ],
  selectedVersionId: successorVersionId,
  resultingCurrentVersion: {
    versionId: successorVersionId,
    queryDigest: successorQueryDigest,
  },
  createdAt: "2026-07-18T00:00:04Z",
  updatedAt: "2026-07-18T00:00:05Z",
};

interface DeterministicApiCalls {
  sessionRequests: unknown[];
  versionRequests: unknown[];
  executionRequests: unknown[];
  turnRequests: unknown[];
}

const installDeterministicApi = async (
  page: Page,
): Promise<DeterministicApiCalls> => {
  const calls: DeterministicApiCalls = {
    sessionRequests: [],
    versionRequests: [],
    executionRequests: [],
    turnRequests: [],
  };
  let currentSession = session(false);
  let currentTimeline = timeline;
  let executionOrdinal = 0;
  let dashboardOrdinal = 0;
  const savedDatasets: DashboardBuilderEntity[] = [];
  const savedWidgets: DashboardBuilderEntity[] = [];
  const savedDashboards: DashboardBuilderEntity[] = [];

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
              requiredModels: [
                "google/gemma-4-e4b",
                "qwen2.5-14b-instruct-mlx",
              ],
              roleModels: {
                query_generate: "google/gemma-4-e4b",
                query_review: "qwen2.5-14b-instruct-mlx",
              },
              stages: ["query_generate", "query_lint", "query_review"],
              unavailableReasons: [],
            },
            {
              id: revisionProfileId,
              label: "Gemma E4B writer + Qwen reviewer",
              available: true,
              revisionCapable: true,
              requiredModels: [
                "google/gemma-4-e4b",
                "qwen2.5-14b-instruct-mlx",
              ],
              roleModels: {
                query_generate: "google/gemma-4-e4b",
                query_review: "qwen2.5-14b-instruct-mlx",
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

    const dashboardCollection = (
      kind: "dataset" | "widget" | "dashboard",
      items: DashboardBuilderEntity[],
    ) => ({
      contractVersion: "catalyst.dashboard-builder.v1",
      kind,
      items,
    });

    if (method === "GET" && path === "/v1/catalyst/dashboard-builder/datasets") {
      await route.fulfill({ status: 200, json: dashboardCollection("dataset", savedDatasets) });
      return;
    }

    if (method === "GET" && path === "/v1/catalyst/dashboard-builder/widgets") {
      await route.fulfill({ status: 200, json: dashboardCollection("widget", savedWidgets) });
      return;
    }

    if (method === "GET" && path === "/v1/catalyst/dashboard-builder/dashboards") {
      await route.fulfill({ status: 200, json: dashboardCollection("dashboard", savedDashboards) });
      return;
    }

    if (method === "POST" && path === "/v1/catalyst/dashboard-builder/datasets") {
      const body = request.postDataJSON() as Record<string, string>;
      dashboardOrdinal += 1;
      const entity: DashboardBuilderEntity = {
        id: "dataset-1",
        versionId: "dataset-version-1",
        ordinal: dashboardOrdinal,
        configuration: {
          title: "Dataset from Query v3",
          source: {
            sessionId: body.sessionId,
            executionId: body.executionId,
          },
        },
        configurationDigest: "6".repeat(64),
        createdAt: "2026-07-18T00:00:10Z",
      };
      savedDatasets.push(entity);
      await route.fulfill({ status: 201, json: entity });
      return;
    }

    if (method === "POST" && path === "/v1/catalyst/dashboard-builder/widgets") {
      const body = request.postDataJSON() as Record<string, string>;
      dashboardOrdinal += 1;
      const entity: DashboardBuilderEntity = {
        id: "widget-1",
        versionId: `widget-version-${savedWidgets.length + 1}`,
        ordinal: dashboardOrdinal,
        configuration: {
          title: body.title || "Dataset from Query v3",
          datasetVersionId: body.datasetVersionId,
          presentationKind: body.presentationKind,
        },
        configurationDigest: "7".repeat(64),
        createdAt: "2026-07-18T00:00:11Z",
      };
      savedWidgets.push(entity);
      await route.fulfill({ status: 201, json: entity });
      return;
    }

    if (method === "POST" && path === "/v1/catalyst/dashboard-builder/dashboards") {
      const body = request.postDataJSON() as {
        title?: string;
        widgetVersionIds: string[];
      };
      dashboardOrdinal += 1;
      const entity: DashboardBuilderEntity = {
        id: "dashboard-1",
        versionId: "dashboard-version-1",
        ordinal: dashboardOrdinal,
        configuration: {
          title: body.title || "Catalyst dashboard",
          widgets: body.widgetVersionIds.map((item) => ({ versionId: item })),
        },
        configurationDigest: "8".repeat(64),
        createdAt: "2026-07-18T00:00:12Z",
      };
      savedDashboards.push(entity);
      await route.fulfill({ status: 201, json: entity });
      return;
    }

    if (
      method === "POST" &&
      path === "/v1/catalyst/dashboard-builder/dashboards/dashboard-version-1/publish"
    ) {
      await route.fulfill({
        status: 201,
        json: {
          status: "bundle_ready",
          dashboard: savedDashboards[0],
          pointer: {
            bundle: {
              fileName: "catalyst-dashboard.zip",
              sha256: "9".repeat(64),
              bytes: 2048,
            },
          },
          downloadPath: "/v1/catalyst/dashboard-builder/dashboards/dashboard-1/bundle",
        },
      });
      return;
    }

    if (method === "POST" && path === "/v1/catalyst/workbench/sessions") {
      calls.sessionRequests.push(request.postDataJSON());
      currentSession = session(false);
      currentTimeline = timeline;
      executionOrdinal = 0;
      await route.fulfill({ status: 201, json: currentSession });
      return;
    }

    if (
      method === "GET" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}`
    ) {
      await route.fulfill({ status: 200, json: currentSession });
      return;
    }

    if (
      method === "GET" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}/turns`
    ) {
      await route.fulfill({ status: 200, json: currentTimeline });
      return;
    }

    if (
      method === "POST" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}/versions`
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      calls.versionRequests.push(body);
      if (body.sql === manualSql && currentSession.currentVersionId === versionId) {
        const manualValidation = validationFor(manualVersion, 1);
        currentSession = {
          ...currentSession,
          currentVersionId: manualVersionId,
          currentVersion: manualVersion,
          versions: [version, manualVersion],
          validations: [manualValidation],
          latestValidation: manualValidation,
          updatedAt: manualValidation.createdAt,
        };
        currentTimeline = {
          ...currentTimeline,
          currentVersion: {
            versionId: manualVersionId,
            queryDigest: manualQueryDigest,
          },
        };
      }
      await route.fulfill({ status: 201, json: currentSession });
      return;
    }

    if (
      method === "POST" &&
      path === `/v1/catalyst/workbench/sessions/${sessionId}/turns`
    ) {
      calls.turnRequests.push(request.postDataJSON());
      const successorValidation = validationFor(successorVersion, 2);
      currentSession = {
        ...currentSession,
        currentVersionId: successorVersionId,
        currentVersion: successorVersion,
        versions: [version, manualVersion, successorVersion],
        validations: [...currentSession.validations, successorValidation],
        latestValidation: successorValidation,
        updatedAt: successorVersion.createdAt,
      };
      currentTimeline = {
        ...timeline,
        currentTurnId: followupTurnId,
        currentVersion: {
          versionId: successorVersionId,
          queryDigest: successorQueryDigest,
        },
        turns: [initialTurn, followupTurn],
      };
      await route.fulfill({ status: 201, json: followupTurn });
      return;
    }

    if (
      method === "POST" &&
      path.startsWith("/v1/catalyst/workbench/versions/") &&
      path.endsWith("/execute")
    ) {
      const body = request.postDataJSON() as Record<string, unknown>;
      calls.executionRequests.push(body);
      executionOrdinal += 1;
      const executedVersion = currentSession.versions.find(
        (candidate) => candidate.versionId === body.versionId,
      );
      if (!executedVersion) {
        await route.fulfill({ status: 404, json: { detail: "Unknown version" } });
        return;
      }
      const includesUnit = executedVersion.versionId === successorVersionId;
      const execution: WorkbenchExecution = {
        contractVersion: "catalyst.workbench.execution.v1",
        queryDigest: executedVersion.queryDigest,
        idempotencyKey: String(body.idempotencyKey),
        validationStatus: "valid",
        query: { sql: executedVersion.sql, parameters: executedVersion.parameters },
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
            ...(includesUnit
              ? [{
                  ordinal: 3,
                  name: "result_unit",
                  databaseType: "text",
                  typeOid: 25,
                  logicalType: "string",
                }]
              : []),
            {
              ordinal: includesUnit ? 4 : 3,
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
              ...(includesUnit
                ? [{ type: "string" as const, value: "copies/ml" }]
                : []),
              { type: "date-time", value: "2026-07-02T00:00:00Z" },
            ],
            ...(includesUnit
              ? [[
                  { type: "string" as const, value: "patient-002" },
                  { type: "decimal" as const, value: "450.0" },
                  { type: "string" as const, value: "copies/ml" },
                  { type: "date-time" as const, value: "2026-07-01T00:00:00Z" },
                ]]
              : []),
          ],
          rowCount: {
            returned: includesUnit ? 2 : 1,
            truncated: false,
            truncationReason: null,
          },
        },
        durationMs: 17,
        executionId: `88888888-8888-4888-8888-${String(executionOrdinal).padStart(12, "0")}`,
        sessionId,
        versionId: executedVersion.versionId,
        ordinal: executionOrdinal,
        completedAt: `2026-07-18T00:00:0${executionOrdinal + 5}Z`,
      };
      currentSession = {
        ...currentSession,
        executions: [...currentSession.executions, execution],
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

const tabTo = async (
  page: Page,
  target: Locator,
  label: string,
  maxTabs = 20,
): Promise<void> => {
  for (let index = 0; index < maxTabs; index += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => {
      const view = globalThis as unknown as {
        document: { activeElement: typeof element | null };
      };
      return element === view.document.activeElement;
    })) {
      const state = await target.evaluate((element) => {
        const view = globalThis as unknown as {
          innerWidth: number;
          innerHeight: number;
          document: {
            elementFromPoint: (
              x: number,
              y: number,
            ) => typeof element | null;
          };
        };
        const rect = element.getBoundingClientRect();
        const x = Math.min(
          view.innerWidth - 1,
          Math.max(0, rect.left + rect.width / 2),
        );
        const y = Math.min(
          view.innerHeight - 1,
          Math.max(0, rect.top + rect.height / 2),
        );
        const hit = view.document.elementFromPoint(x, y);
        return {
          focusVisible: element.matches(":focus-visible"),
          fullyInViewport:
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= view.innerHeight &&
            rect.right <= view.innerWidth,
          unobscured: Boolean(
            hit && (element.contains(hit) || hit.contains(element)),
          ),
        };
      });
      expect(state, `${label} keyboard-focus state`).toEqual({
        focusVisible: true,
        fullyInViewport: true,
        unobscured: true,
      });
      return;
    }
  }
  throw new Error(`${label} was not reachable within ${maxTabs} Tab presses`);
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
    await page.getByRole("textbox", { name: "SQL query" }).fill(manualSql);
  }

  await page.getByRole("button", { name: "Validate query" }).click();
  if (useMockApi) {
    await expect(page.getByText("Valid", { exact: true })).toBeVisible();
    await expect.poll(() => calls?.versionRequests.length).toBe(1);
    expect(calls?.versionRequests[0]).toEqual({
      contractVersion: "catalyst.workbench.version.request.v1",
      parentVersionId: versionId,
      parentQueryDigest: queryDigest,
      sql: manualSql,
      parameters,
      expectedColumns: [],
    });
  } else {
    await expect(page.getByRole("heading", { name: "Validation" })).toBeVisible();
  }

  await page.getByRole("button", { name: "Run query" }).click();
  const execution = page.getByRole("region", { name: "Latest execution" });
  await expect(execution).toBeVisible();

  if (useMockApi) {
    await expect(
      execution.getByRole("heading", { name: "Results from Query v2" }),
    ).toBeVisible();
    await expect(execution.getByText("patient-001", { exact: true })).toBeVisible();
    await expect(execution.getByText("1200.0", { exact: true })).toBeVisible();
    await expect(
      page.getByText(/Execution summary: Query v2 · Run 1 · 1 row/i),
    ).toBeVisible();
    await expect(
      page.getByText(/Result row values are not included in model context/i),
    ).toBeVisible();
    await expect.poll(() => calls?.versionRequests.length).toBe(2);
    await expect.poll(() => calls?.executionRequests.length).toBe(1);
    expect(calls?.executionRequests[0]).toMatchObject({
      contractVersion: "catalyst.workbench.execute.request.v1",
      versionId: manualVersionId,
      queryDigest: manualQueryDigest,
      idempotencyKey: expect.any(String),
    });

    const provenance = page.getByRole("region", { name: "Run provenance" });
    await expect(provenance.getByText("google/gemma-4-e4b", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText("qwen2.5-14b-instruct-mlx", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText("pipeline-run-77", { exact: true }))
      .toBeVisible();
    await expect(provenance.getByText(sessionId, { exact: true })).toBeVisible();
    await expect.poll(
      () => page.evaluate(() =>
        localStorage.getItem("catalyst.workbench.activeSessionId")),
    ).toBe(sessionId);

    await page.getByRole("combobox", { name: "Model profile" }).selectOption(
      revisionProfileId,
    );
    await page.getByRole("textbox", { name: "Follow-up instruction" }).fill(
      "Include the result unit in the current query",
    );
    await page.getByRole("button", { name: "Generate next query" }).click();

    await expect(
      page.getByRole("heading", { name: "Refine Query v3" }),
    ).toBeVisible();
    await expect(page.getByRole("textbox", { name: "SQL query" }))
      .toContainText("result_unit");
    await expect.poll(() => calls?.turnRequests.length).toBe(1);
    expect(calls?.turnRequests[0]).toMatchObject({
      contractVersion: "catalyst.workbench.turn.request.v1",
      instruction: "Include the result unit in the current query",
      profileId: revisionProfileId,
      observedBase: {
        versionId: manualVersionId,
        queryDigest: manualQueryDigest,
      },
      editorSnapshot: {
        sql: manualSql,
        parameters,
        expectedColumns: [],
        editorDigest: manualQueryDigest,
      },
    });
    await expect(
      execution.getByRole("heading", { name: "Results from Query v2" }),
    ).toBeVisible();
    await expect(execution.getByText("Stale — editor has changes", { exact: true }))
      .toBeVisible();

    const switchedProvenance = page.getByRole("region", {
      name: "Run provenance",
    });
    await expect(
      switchedProvenance.getByText("Gemma E4B writer + Qwen reviewer", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(switchedProvenance.getByText("google/gemma-4-e4b", { exact: true }))
      .toBeVisible();
    await expect(
      switchedProvenance.getByText("qwen2.5-14b-instruct-mlx", { exact: true }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Run query" }).click();
    await expect(
      execution.getByRole("heading", { name: "Results from Query v3" }),
    ).toBeVisible();
    await expect(execution.getByText("patient-002", { exact: true })).toBeVisible();
    await expect(execution.getByText("copies/ml", { exact: true }).first())
      .toBeVisible();
    await expect.poll(() => calls?.executionRequests.length).toBe(2);
    expect(calls?.executionRequests[1]).toMatchObject({
      versionId: successorVersionId,
      queryDigest: successorQueryDigest,
    });

    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Refine Query v3" }),
    ).toBeVisible();
    await expect(page.getByRole("textbox", { name: "SQL query" }))
      .toContainText("result_unit");
    await expect(
      page.getByRole("button", { name: /Query turn 2/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Latest execution" })
        .getByRole("heading", { name: "Results from Query v3" }),
    ).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Model profile" }))
      .toHaveValue(revisionProfileId);

    const dashboardBuilder = page.getByRole("region", {
      name: "Dashboard builder",
    });
    await dashboardBuilder.getByRole("textbox", { name: "Dashboard title" })
      .fill("Virology dashboard");
    await dashboardBuilder.getByRole("textbox", { name: "Widget title" })
      .fill("Latest laboratory results");
    await dashboardBuilder.getByRole("combobox", { name: "Visualization" })
      .selectOption("time_series_line");
    await dashboardBuilder.getByRole("button", { name: "Add widget" }).click();
    await expect(dashboardBuilder.getByText("1 widget ready", { exact: true }))
      .toBeVisible();
    await expect(
      dashboardBuilder.getByText("Latest laboratory results", { exact: true }),
    ).toBeVisible();
    await dashboardBuilder.getByRole("button", { name: "Publish to Superset" })
      .click();
    await expect(dashboardBuilder.getByText("Superset bundle ready", { exact: true }))
      .toBeVisible();
    await expect(dashboardBuilder.getByRole("link", { name: "Download bundle" }))
      .toBeVisible();
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

  if (useMockApi) {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole("button", { name: "Expand" }).click();
    await page.getByRole("button", { name: "Minimize" }).focus();

    const keyboardTargets: Array<[Locator, string]> = [
      [
        page.getByRole("textbox", { name: "Follow-up instruction" }),
        "follow-up instruction",
      ],
      [page.getByRole("combobox", { name: "Model profile" }), "model profile"],
      [
        page.getByRole("button", { name: "Generate next query" }),
        "generate next query",
      ],
      [page.getByRole("button", { name: "New session" }), "new session"],
      [page.getByRole("textbox", { name: "SQL query" }), "SQL editor"],
      [page.getByRole("button", { name: "Wrap lines" }), "wrap lines"],
      [page.getByRole("button", { name: "Format SQL" }), "format SQL"],
      [page.getByRole("button", { name: "Clear draft" }), "clear draft"],
      [page.getByRole("button", { name: "Validate query" }), "validate query"],
      [page.getByRole("button", { name: "Run query" }), "run query"],
      [
        page.getByRole("textbox", { name: "Dashboard title" }),
        "dashboard title",
      ],
      [page.getByRole("textbox", { name: "Widget title" }), "widget title"],
      [
        page.getByRole("combobox", { name: "Visualization" }),
        "visualization",
      ],
      [page.getByRole("button", { name: "Add widget" }), "add widget"],
      [
        page.getByRole("button", { name: "Remove", exact: true }),
        "remove widget",
      ],
      [
        page.getByRole("button", { name: "Publish to Superset" }),
        "publish to Superset",
      ],
    ];
    for (const [target, label] of keyboardTargets) {
      await tabTo(page, target, label);
    }

    // A 1280 CSS-pixel desktop at 200% browser zoom reflows to a 640 CSS-pixel
    // layout viewport. The manual acceptance used actual browser zoom; this
    // deterministic regression preserves the equivalent layout boundary.
    await page.setViewportSize({ width: 640, height: 720 });
    await expect.poll(
      () => page.evaluate<boolean>(
        "document.documentElement.scrollWidth <= window.innerWidth",
      ),
    ).toBe(true);
    await expect(page.getByRole("textbox", { name: "Follow-up instruction" }))
      .toBeVisible();
    await expect(page.getByRole("textbox", { name: "SQL query" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Validate query" }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "Run query" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Dashboard title" }))
      .toBeVisible();
    await expect(page.getByRole("combobox", { name: "Visualization" }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "Add widget" }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "Publish to Superset" }))
      .toBeVisible();
  }
});
