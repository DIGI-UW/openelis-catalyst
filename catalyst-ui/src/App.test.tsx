import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { CatalystApi } from "./features/query/api";
import type {
  WorkbenchEditorCatalog,
  WorkbenchExecution,
  WorkbenchQueryVersion,
  WorkbenchSession,
  WorkbenchValidation,
} from "./features/query/types";
import {
  executionOutcome,
  policyOutcome,
  preview,
  queryOutcome,
  QUESTION,
  table,
} from "./features/query/test/fixtures";

const makeApi = (): CatalystApi => ({
  submitQuestion: vi.fn(),
  executePreview: vi.fn(),
  pollExecution: vi.fn(),
});

const queryOptions = {
  contractVersion: "catalyst.query-options.v1" as const,
  defaultProfileId: "catalyst-query-gemma-e4b",
  profiles: [
    {
      id: "catalyst-query-gemma-e4b",
      label: "Catalyst governed query — Gemma 4 E4B",
      available: true,
      requiredModels: ["gemma-e4b"],
      roleModels: {
        query_generate: "gemma-e4b",
        query_review: "gemma-e4b",
      },
      stages: ["context", "query_generate", "query_review", "query_finalize"],
      unavailableReasons: [],
    },
    {
      id: "catalyst-query-split-models",
      label: "Split generation and review",
      available: true,
      requiredModels: ["generation-model", "review-model"],
      roleModels: {
        query_review: "review-model",
        query_generate: "generation-model",
      },
      stages: ["context", "query_generate", "query_review", "query_finalize"],
      unavailableReasons: [],
    },
    {
      id: "catalyst-query-offline",
      label: "Offline research profile",
      available: false,
      requiredModels: ["offline-model"],
      roleModels: {
        query_generate: "offline-model",
        query_review: "offline-model",
      },
      stages: ["context", "query_generate", "query_review", "query_finalize"],
      unavailableReasons: ["offline-model is not loaded"],
    },
  ],
};

const workbenchVersion: WorkbenchQueryVersion = {
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId: "d801dc1d-fc94-435b-bee6-2b45c3173af1",
  sessionId: "2bed91de-fa7d-4ffa-b4ae-0a454a883930",
  parentVersionId: null,
  ordinal: 1,
  authorType: "model",
  sql: "SELECT COUNT(DISTINCT patient_id) FROM analytics.lab_result_fact_v1 WHERE test_name = :test_name AND result_value > :minimum_result",
  parameters: [
    { name: "test_name", type: "string", source: "question", value: "Viral Load" },
    { name: "minimum_result", type: "number", source: "question", value: 1000 },
  ],
  expectedColumns: [],
  queryDigest: "a".repeat(64),
  provenance: {},
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-07-17T00:00:01Z",
};

const workbenchValidation: WorkbenchValidation = {
  contractVersion: "catalyst.workbench.validation.v1",
  queryDigest: workbenchVersion.queryDigest,
  validatorRevision: "catalyst.workbench.validator.v1",
  validatorDigest: "b".repeat(64),
  status: "invalid",
  advisory: true,
  checks: [
    {
      name: "gateway_sql_policy",
      status: "failed",
      findingIds: ["finding-111111111111111111111111"],
    },
  ],
  findings: [
    {
      contractVersion: "catalyst.workbench.finding.v1",
      findingId: "finding-111111111111111111111111",
      ruleCode: "policy.unit_not_grounded",
      severity: "error",
      stage: "gateway_sql_policy",
      message: "The requested count/ml unit does not match the catalog unit copies/ml.",
      path: "$.sql",
      astUnit: null,
      span: null,
      evidence: { questionUnit: "count/ml", catalogUnit: "copies/ml" },
      suggestedAction: "Review the unit and edit the query if needed.",
      repairability: "manual",
      validatorRevision: "catalyst.workbench.validator.v1",
    },
  ],
  durationMs: 2,
  validationId: "validation-1",
  sessionId: workbenchVersion.sessionId,
  versionId: workbenchVersion.versionId,
  ordinal: 1,
  createdAt: "2026-07-17T00:00:02Z",
};

const workbenchSession: WorkbenchSession = {
  contractVersion: "catalyst.workbench.session.v1",
  sessionId: workbenchVersion.sessionId,
  question: QUESTION,
  profileId: "catalyst-query-gemma-e4b",
  datasetId: "catalyst-openelis-cohort-v1",
  datasetVersion: "pipeline-run-77",
  catalogVersion: "analytics-catalog-v1",
  currentVersionId: workbenchVersion.versionId,
  browserState: {},
  provenance: {
    catalystTraceId: "catalyst-trace-1",
    generationRawOutput: "{latest malformed model output}",
    generationOutcome: {
      contractVersion: "catalyst.query.v1",
      diagnosticCandidate: {
        executable: false,
        candidate: {
          status: "ready",
          sql: workbenchVersion.sql,
          parameters: workbenchVersion.parameters,
        },
        rawOutput: "{latest malformed model output}",
        attempts: [
          {
            attempt: 2,
            status: "failed",
            findings: [
              {
                code: "contract.schema",
                path: "$.parameters[1]",
                message: "'name' is a required property",
              },
            ],
          },
        ],
      },
    },
    profileSnapshot: {
      profileId: "catalyst-query-gemma-e4b",
      roleModels: { query_generate: "gemma-e4b", query_review: "gemma-e4b" },
    },
  },
  status: "active",
  createdAt: "2026-07-17T00:00:00Z",
  updatedAt: "2026-07-17T00:00:02Z",
  versions: [workbenchVersion],
  currentVersion: workbenchVersion,
  validations: [workbenchValidation],
  latestValidation: workbenchValidation,
  executions: [],
};

const unresolvedRawSql =
  "SELECT COUNT(DISTINCT patient_id) AS count FROM analytics.lb_result_fact_v1 WHERE test_name = :test_name AND result_value > :threshold";
const unresolvedRawOutput = JSON.stringify({
  status: "ready",
  sql: unresolvedRawSql,
  parameters: [
    { value: "Viral Load", type: "string" },
    { value: 1000, type: "integer" },
  ],
});
const unresolvedRawSession = {
  ...workbenchSession,
  sessionId: "902bd844-e8f1-403d-90ee-8fccd9417f99",
  profileId: "catalyst-query-gemma-4-12b",
  currentVersionId: null,
  provenance: {
    ...workbenchSession.provenance,
    generationRawOutput: unresolvedRawOutput,
    generationOutcome: {
      contractVersion: "catalyst.query.v1",
      diagnosticCandidate: { executable: false, rawOutput: unresolvedRawOutput },
    },
  },
  draftSeed: {
    status: "unresolved",
    source: "raw_model_output",
    sql: unresolvedRawSql,
    parameters: [
      { name: "", type: "string", source: "human", value: "Viral Load" },
      { name: "", type: "integer", source: "human", value: 1000 },
    ],
    unresolvedPaths: [
      "$.parameters[0].name",
      "$.parameters[0].source",
      "$.parameters[1].name",
      "$.parameters[1].source",
    ],
  },
  versions: [],
  currentVersion: null,
  validations: [],
  latestValidation: null,
  executions: [],
} satisfies WorkbenchSession;

const editorCatalog: WorkbenchEditorCatalog = {
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
          columns: [
            { name: "patient_id", logicalType: "string" },
            { name: "result_value", logicalType: "decimal" },
            { name: "test_name", logicalType: "string" },
          ],
        },
      ],
    },
  ],
};

const childWorkbenchSession = (validationStatus: "invalid" | "warning" | "valid") => {
  const currentVersion: WorkbenchQueryVersion = {
    ...workbenchVersion,
    versionId: "3d72ce15-c09c-48eb-a6f1-294ac7f28d12",
    parentVersionId: workbenchVersion.versionId,
    ordinal: 2,
    authorType: "human",
    queryDigest: "c".repeat(64),
    parameters: workbenchVersion.parameters.map((parameter) => ({ ...parameter })),
  };
  const latestValidation: WorkbenchValidation = {
    ...workbenchValidation,
    validationId: "validation-2",
    versionId: currentVersion.versionId,
    queryDigest: currentVersion.queryDigest,
    ordinal: 1,
    status: validationStatus,
    findings: validationStatus === "valid" ? [] : workbenchValidation.findings,
    checks: validationStatus === "valid" ? [] : workbenchValidation.checks,
  };
  return {
    ...workbenchSession,
    currentVersionId: currentVersion.versionId,
    currentVersion,
    versions: [...workbenchSession.versions, currentVersion],
    validations: [...workbenchSession.validations, latestValidation],
    latestValidation,
  } satisfies WorkbenchSession;
};

const failedWorkbenchExecution: WorkbenchExecution = {
  contractVersion: "catalyst.workbench.execution.v1",
  queryDigest: "c".repeat(64),
  idempotencyKey: "run-1",
  validationStatus: "invalid",
  query: {
    sql: workbenchVersion.sql,
    parameters: workbenchVersion.parameters,
  },
  statementTimeoutMs: 5000,
  maxRows: 1000,
  replayed: false,
  status: "failed",
  databaseDiagnostic: {
    sqlstate: "42703",
    severity: "ERROR",
    message: "column result_count does not exist",
    detail: "The generated identifier is not present in the loaded view.",
    hint: "Use result_value.",
    position: 42,
  },
  durationMs: 4,
  executionId: "execution-1",
  sessionId: workbenchSession.sessionId,
  versionId: "3d72ce15-c09c-48eb-a6f1-294ac7f28d12",
  ordinal: 1,
  completedAt: "2026-07-17T00:00:04Z",
};

const askQuestion = async () => {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Question"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Generate query" }));
  return user;
};

beforeEach(() => {
  localStorage.clear();
});

describe("Catalyst query workflow", () => {
  it("keeps the demo boundary visible from the initial state", () => {
    render(<App api={makeApi()} />);

    expect(screen.getByText("Demo environment")).toBeVisible();
    expect(
      screen.getByText(/demo data only; not for clinical decision-making/i),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeDisabled();
  });

  it("keeps the Ask OpenELIS input directly focusable", async () => {
    const user = userEvent.setup();
    render(<App api={makeApi()} />);

    await user.click(screen.getByRole("button", { name: "Ask OpenELIS" }));

    expect(screen.getByLabelText("Question")).toHaveFocus();
  });

  it("submits a question and presents the authoritative preview", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    const user = await askQuestion();

    expect(api.submitQuestion).toHaveBeenCalledWith(
      QUESTION,
      "catalyst-query-gemma-e4b",
    );
    expect(await screen.findByRole("heading", { name: "Review query" })).toBeVisible();
    expect(screen.getByLabelText("Question")).toBeDisabled();
    expect(screen.getByLabelText("Model profile")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Ask OpenELIS" }));
    expect(screen.getByRole("heading", { name: "Ask OpenELIS" })).toHaveFocus();
    expect(screen.getByLabelText("Question")).toBeDisabled();
    expect(screen.getByLabelText("Generated SQL")).toHaveTextContent(
      "SELECT collected_on, result_value FROM analytics.vw_viral_load_results WHERE result_value >= :minimum_result",
    );
    expect(screen.getByText("minimum_result")).toBeVisible();
    expect(screen.getByText("integer")).toBeVisible();
    expect(screen.getByText("80")).toBeVisible();
    expect(screen.getByRole("button", { name: "Accept and run" })).toBeEnabled();
    const trace = screen.getByLabelText("Reasoning trace");
    expect(within(trace).getAllByText("gemma-e4b")).toHaveLength(2);
    expect(within(trace).getAllByText("query review")).toHaveLength(2);
    expect(within(trace).getByText(/structured stage and validation summary/i)).toBeVisible();
  });

  it.each([
    {
      mode: "workbench",
      title: "Generating workbench draft",
      message:
        "Med-Agent Hub is generating an editable SQL draft with the selected profile.",
    },
    {
      mode: "legacy preview",
      title: "Preparing preview",
      message: "Catalyst is validating the question and proposed query.",
    },
  ] as const)(
    "uses $mode loading copy while generation is in progress",
    async ({ mode, title, message }) => {
      const api = makeApi();
      const pendingRequest = new Promise<never>(() => undefined);
      if (mode === "workbench") {
        api.createWorkbenchSession = vi.fn().mockReturnValue(pendingRequest);
        api.createWorkbenchVersion = vi.fn();
        api.executeWorkbenchVersion = vi.fn();
      } else {
        vi.mocked(api.submitQuestion).mockReturnValue(pendingRequest);
      }
      render(<App api={api} />);

      await askQuestion();

      expect(await screen.findByRole("heading", { name: title })).toBeVisible();
      expect(screen.getByText(message)).toBeVisible();
    },
  );

  it("shows the dataset browser and uses the Hub-owned available profile", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    api.getDatasetOverview = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.dataset-overview.v1",
      datasetId: "catalyst-openelis-cohort-v1",
      synthetic: true,
      patients: 96,
      results: 1152,
      testTypes: 9,
      firstObservedAt: "2025-07-15T04:00:00Z",
      lastObservedAt: "2026-04-27T04:00:00Z",
      tests: [
        {
          testName: "Viral Load",
          unit: "copies/ml",
          results: 384,
          patients: 96,
          minimum: "30",
          median: "900",
          maximum: "35000",
        },
      ],
      exampleQuestions: ["Show viral load results since 2026-01-01"],
    });
    api.getDatasetRows = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.dataset-rows.v1",
      total: 1,
      limit: 25,
      offset: 0,
      rows: [
        {
          observationId: "observation-1",
          patientId: "patient-123456789",
          testName: "Viral Load",
          value: "9000",
          unit: "copies/ml",
          observedAt: "2026-04-27T04:00:00Z",
          issuedAt: "2026-04-27T04:00:00Z",
          turnaroundMinutes: "120",
        },
      ],
    });
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    expect(await screen.findByText("1,152")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Available OpenELIS laboratory data" }),
    ).toBeVisible();
    expect(screen.queryByText("Synthetic laboratory dataset")).not.toBeInTheDocument();
    expect(screen.queryByText("Example questions")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Show viral load results since 2026-01-01"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/test types and numeric distributions/i),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveAttribute(
      "placeholder",
      "Describe the laboratory data you want to explore",
    );
    expect(screen.getByLabelText("Model profile")).toHaveValue(
      "catalyst-query-gemma-e4b",
    );
    const profileSelector = screen.getByLabelText("Model profile");
    expect(
      within(profileSelector).getByRole("option", {
        name: "Catalyst governed query — Gemma 4 E4B — gemma-e4b",
      }),
    ).toBeInTheDocument();
    expect(
      within(profileSelector).queryByRole("option", {
        name: /gemma-e4b, gemma-e4b/,
      }),
    ).not.toBeInTheDocument();
    expect(
      within(profileSelector).getByRole("option", {
        name: "Split generation and review — generation-model, review-model",
      }),
    ).toBeInTheDocument();
    expect(
      within(profileSelector).queryByRole("option", {
        name: "Offline research profile",
      }),
    ).not.toBeInTheDocument();

    const user = userEvent.setup();
    const browserToggle = screen.getByRole("button", {
      name: "Browse available laboratory records",
    });
    expect(browserToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(browserToggle);
    expect(browserToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("9000 copies/ml")).toBeVisible();
    const patientFilter = screen.getByLabelText("Patient FHIR ID");
    await user.type(patientFilter, "patient-123");
    await user.click(browserToggle);
    expect(browserToggle).toHaveAttribute("aria-expanded", "false");
    await user.click(browserToggle);
    expect(browserToggle).toHaveAttribute("aria-expanded", "true");
    expect(patientFilter).toHaveValue("patient-123");
    await user.type(
      screen.getByLabelText("Question"),
      "Show viral load results since 2026-01-01",
    );
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    expect(api.submitQuestion).toHaveBeenCalledWith(
      "Show viral load results since 2026-01-01",
      "catalyst-query-gemma-e4b",
    );
  });

  it("opens the editable workbench and keeps invalid model evidence runnable", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    api.getWorkbenchCatalog = vi.fn().mockResolvedValue(editorCatalog);
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    await askQuestion();

    expect(api.createWorkbenchSession).toHaveBeenCalledWith(
      QUESTION,
      "catalyst-query-gemma-e4b",
    );
    expect(api.submitQuestion).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("heading", { name: "Query workbench" }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toBeVisible();
    expect(screen.getByText("policy.unit_not_grounded")).toBeVisible();
    expect(screen.getByText(/validation is advisory/i)).toBeVisible();
    expect(screen.getByText("'name' is a required property")).toBeVisible();
    expect(screen.getByText("{latest malformed model output}")).toBeVisible();
    expect(screen.getByRole("button", { name: "Validate query" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run query" })).toBeEnabled();
  });

  it("hydrates parseable raw JSON as an explicitly unresolved manual draft", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    api.createWorkbenchSession = vi.fn().mockResolvedValue(unresolvedRawSession);
    api.createWorkbenchVersion = vi.fn().mockRejectedValue(
      new Error("Stop after request capture."),
    );
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    expect(await screen.findByText("Unresolved model draft")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(
      unresolvedRawSql,
    );
    expect(screen.getByLabelText("Parameter 1 name")).toHaveValue("");
    expect(screen.getByLabelText("Parameter 1 value")).toHaveValue("Viral Load");
    expect(screen.getByLabelText("Parameter 2 name")).toHaveValue("");
    expect(screen.getByLabelText("Parameter 2 value")).toHaveValue("1000");
    expect(screen.getByText(unresolvedRawOutput)).toBeVisible();

    await user.type(screen.getByLabelText("Parameter 1 name"), "test_name");
    await user.type(screen.getByLabelText("Parameter 2 name"), "threshold");
    await user.click(screen.getByRole("button", { name: "Validate query" }));

    await waitFor(() =>
      expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
        unresolvedRawSession.sessionId,
        {
          sql: unresolvedRawSql,
          parameters: [
            {
              name: "test_name",
              type: "string",
              source: "human",
              value: "Viral Load",
            },
            {
              name: "threshold",
              type: "integer",
              source: "human",
              value: 1000,
            },
          ],
          expectedColumns: [],
        },
      ),
    );
  });

  it("restores the unresolved raw editor seed after refresh", async () => {
    const api = makeApi();
    api.getWorkbenchSession = vi.fn().mockResolvedValue(unresolvedRawSession);
    localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      unresolvedRawSession.sessionId,
    );

    render(<App api={api} />);

    expect(await screen.findByText("Unresolved model draft")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(
      unresolvedRawSql,
    );
    expect(screen.getByLabelText("Parameter 2 value")).toHaveValue("1000");
  });

  it("prefers an immutable current version over a stale raw draft seed", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    api.createWorkbenchSession = vi.fn().mockResolvedValue({
      ...workbenchSession,
      draftSeed: unresolvedRawSession.draftSeed,
    });
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(
      workbenchVersion.sql,
    );
    expect(screen.queryByText("Unresolved model draft")).not.toBeInTheDocument();
  });

  it("persists a manually corrected parameter as a new version before validation", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn().mockResolvedValue(
      childWorkbenchSession("warning"),
    );
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    const parameterName = await screen.findByLabelText("Parameter 2 name");
    await user.clear(parameterName);
    await user.type(parameterName, "threshold");
    await user.click(screen.getByRole("button", { name: "Validate query" }));

    await waitFor(() =>
      expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
        workbenchSession.sessionId,
        {
          parentVersionId: workbenchVersion.versionId,
          parentQueryDigest: workbenchVersion.queryDigest,
          sql: workbenchVersion.sql,
          parameters: [
            workbenchVersion.parameters[0],
            {
              ...workbenchVersion.parameters[1],
              name: "threshold",
              source: "human",
            },
          ],
          expectedColumns: [],
        },
      ),
    );
    expect(api.executeWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("persists and executes the exact draft even when validation is invalid", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    const child = childWorkbenchSession("invalid");
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn().mockResolvedValue(child);
    api.executeWorkbenchVersion = vi.fn().mockResolvedValue(
      failedWorkbenchExecution,
    );
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    await user.click(await screen.findByRole("button", { name: "Run query" }));

    expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
      workbenchSession.sessionId,
      {
        parentVersionId: workbenchVersion.versionId,
        parentQueryDigest: workbenchVersion.queryDigest,
        sql: workbenchVersion.sql,
        parameters: workbenchVersion.parameters,
        expectedColumns: [],
      },
    );
    expect(api.executeWorkbenchVersion).toHaveBeenCalledWith(
      child.currentVersion!.versionId,
      child.currentVersion!.queryDigest,
      expect.any(String),
    );
    const databaseMessage = await screen.findByText(
      "column result_count does not exist",
    );
    expect(databaseMessage).toBeVisible();
    const diagnostic = databaseMessage.closest("[role='alert']");
    expect(diagnostic).not.toBeNull();
    expect(within(diagnostic as HTMLElement).getByText("SQLSTATE")).toBeVisible();
    expect(within(diagnostic as HTMLElement).getByText("42703")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run query" })).toBeEnabled();
  });

  it("restores the active server workbench session after a refresh", async () => {
    const api = makeApi();
    const restoredSession = {
      ...workbenchSession,
      profileId: "catalyst-query-split-models",
    } satisfies WorkbenchSession;
    let resolveQueryOptions: (options: typeof queryOptions) => void = () => undefined;
    api.getQueryOptions = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveQueryOptions = resolve;
        }),
    );
    api.getWorkbenchSession = vi.fn().mockResolvedValue(restoredSession);
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      restoredSession.sessionId,
    );

    render(<App api={api} />);

    expect(api.getWorkbenchSession).toHaveBeenCalledWith(
      restoredSession.sessionId,
      expect.any(AbortSignal),
    );
    expect(
      await screen.findByRole("heading", { name: "Query workbench" }),
    ).toBeVisible();
    expect(screen.getByLabelText("Question")).toHaveValue(QUESTION);
    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(
      "COUNT(DISTINCT patient_id)",
    );
    resolveQueryOptions(queryOptions);
    expect(await screen.findByLabelText("Model profile")).toHaveValue(
      restoredSession.profileId,
    );
  });

  it("shows clarification without exposing an acceptance action", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(
      queryOutcome("needs_clarification"),
    );
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Clarification needed" }),
    ).toBeVisible();
    expect(screen.getByText("Which facility should be included?")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it("falls back from an unavailable default and omits unavailable profiles", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue({
      ...queryOptions,
      defaultProfileId: "catalyst-query-offline",
    });
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    const selector = await screen.findByLabelText("Model profile");
    expect(selector).toHaveValue("catalyst-query-gemma-e4b");
    expect(
      within(selector).queryByRole("option", {
        name: "Offline research profile",
      }),
    ).not.toBeInTheDocument();

    await askQuestion();
    expect(api.submitQuestion).toHaveBeenCalledWith(
      QUESTION,
      "catalyst-query-gemma-e4b",
    );
  });

  it("keeps the no-profile fallback when query options cannot be loaded", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockRejectedValue(
      new Error("Query options are unavailable."),
    );
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    expect(screen.queryByLabelText("Model profile")).not.toBeInTheDocument();
    await askQuestion();

    expect(api.submitQuestion).toHaveBeenCalledWith(QUESTION);
    expect(screen.queryByText("Offline research profile")).not.toBeInTheDocument();
  });

  it.each([
    ["unsupported", "Question unsupported"],
    ["rejected", "Question rejected"],
  ] as const)("shows a stable %s outcome", async (status, heading) => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(queryOutcome(status));
    render(<App api={api} />);

    await askQuestion();

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(screen.getByText(`The question was ${status}.`)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it("shows a rejected generated candidate and lint feedback without an execution action", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(queryOutcome("rejected"));
    render(<App api={api} />);

    await askQuestion();

    expect(await screen.findByText("Generated candidate")).toBeVisible();
    expect(screen.getByText("Not executable")).toBeVisible();
    expect(screen.getByLabelText("Rejected generated SQL")).toHaveTextContent(
      "result_value > 1000",
    );
    expect(
      screen.getByText("policy.unbound_predicate_literal"),
    ).toBeVisible();
    expect(screen.getByText(/Replace 1000 with a named parameter/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it("distinguishes a Catalyst policy rejection and its violations", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(policyOutcome);
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Catalyst policy rejection" }),
    ).toBeVisible();
    expect(screen.getByText(policyOutcome.violations[0]!.message)).toBeVisible();
    expect(screen.getByText("Trace: cat-trace-policy")).toBeVisible();
  });

  it("accepts a preview and renders typed table data and provenance", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(table);
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    const results = await screen.findByRole("region", { name: "Query results" });
    expect(within(results).getByText("1200")).toBeVisible();
    expect(within(results).getByText("450")).toBeVisible();
    expect(within(results).getByText("80")).toBeVisible();
    expect(within(results).getByText("result_value (copies/mL)")).toBeVisible();
    expect(api.executePreview).toHaveBeenCalledWith(
      preview,
      expect.any(String),
    );

    const provenance = screen.getByRole("region", { name: "Provenance" });
    expect(within(provenance).getByText("cat-trace-123")).toBeVisible();
    expect(within(provenance).getByText("hub-trace-456")).toBeVisible();
    expect(within(provenance).getByText("pipeline-run-77")).toBeVisible();
    expect(within(provenance).getByText("catalyst-query-gemma-e4b")).toBeVisible();
    expect(screen.getByText("Demo environment")).toBeVisible();
    expect(screen.getByLabelText("Question")).toBeEnabled();
    expect(screen.getByLabelText("Model profile")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Ask OpenELIS" }));
    expect(screen.getByLabelText("Question")).toHaveFocus();
  });

  it("polls the accepted execution until the table is ready", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(
      executionOutcome("in_progress"),
    );
    let resolvePoll: (result: typeof table) => void = () => undefined;
    vi.mocked(api.pollExecution).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePoll = resolve;
        }),
    );
    render(<App api={api} pollIntervalMs={1} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Query running" }),
    ).toBeVisible();
    expect(screen.getByLabelText("Question")).toBeDisabled();
    expect(screen.getByLabelText("Model profile")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeDisabled();
    await waitFor(() => expect(api.pollExecution).toHaveBeenCalled());
    resolvePoll(table);
    expect(await screen.findByRole("region", { name: "Query results" })).toBeVisible();
    expect(screen.getByLabelText("Question")).toBeEnabled();
    expect(screen.getByLabelText("Model profile")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeEnabled();
    expect(api.pollExecution).toHaveBeenCalledWith(
      preview.previewId,
      expect.any(String),
      expect.any(AbortSignal),
    );
  });

  it.each([
    ["conflict", "Execution conflict"],
    ["failed", "Execution failed"],
  ] as const)("renders the terminal %s state", async (status, heading) => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(executionOutcome(status));
    render(<App api={api} />);

    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(screen.getByText(`Execution ${status}.`)).toBeVisible();
    expect(screen.getByRole("button", { name: "Start a new query" })).toBeEnabled();
    expect(screen.getByLabelText("Question")).toBeEnabled();
  });

  it("surfaces request failures and allows a retry", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockRejectedValue(
      new Error("Gateway is unavailable."),
    );
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Request failed" }),
    ).toBeVisible();
    expect(screen.getByText("Gateway is unavailable.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeEnabled();
  });

  it("trims a submitted question", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), `  ${QUESTION}  `);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    await waitFor(() => expect(api.submitQuestion).toHaveBeenCalledWith(QUESTION));
  });
});
