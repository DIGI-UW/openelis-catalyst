import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { CatalystApi } from "./features/query/api";
import { formatPostgresqlSql } from "./features/query/components/sqlEditorSupport";
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

// The editor lays model SQL out for reading on arrival, and a run persists what
// the editor holds, so these expectations are written through the same formatter
// the app uses rather than as hand-copied strings.
const asEditorText = (sql: string) => formatPostgresqlSql(sql);

// The editor renders SQL across lines, so its textContent has no spaces at the
// line breaks. These assertions are about *which* query is on screen, not how it
// is laid out, so both sides are compared with whitespace removed.
const showsQuery = (element: HTMLElement, sql: string) => {
  const strip = (value: string) => value.replace(/\s+/g, "");
  expect(strip(element.textContent ?? "")).toContain(strip(sql));
};

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
  name: QUESTION,
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
          sql: asEditorText(workbenchVersion.sql),
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

const notebookQueryOptions = {
  ...queryOptions,
  defaultProfileId: "catalyst-query-gemma-4-12b",
  profiles: [
    {
      id: "catalyst-query-gemma-4-12b",
      label: "Gemma writer + Qwen reviewer",
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
    ...queryOptions.profiles.map((profile) => ({
      ...profile,
      revisionCapable: profile.id === "catalyst-query-split-models",
    })),
  ],
};

const notebookSession = {
  ...workbenchSession,
  profileId: "catalyst-query-gemma-4-12b",
  provenance: {
    ...workbenchSession.provenance,
    profileSnapshot: {
      profileId: "catalyst-query-gemma-4-12b",
      roleModels: {
        query_generate: "gemma-4-12b",
        query_review: "qwen2.5-14b",
      },
    },
  },
} satisfies WorkbenchSession;

const notebookProfileSnapshot = {
  profileId: "catalyst-query-gemma-4-12b",
  profileName: "Gemma writer + Qwen reviewer",
  profileDigest: "d".repeat(64),
  writer: { modelId: "gemma-4-12b" },
  reviewer: { modelId: "qwen2.5-14b" },
  omissions: [],
};

const initialNotebookTurn = {
  contractVersion: "catalyst.workbench.turn.v1" as const,
  sessionId: notebookSession.sessionId,
  turnId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  ordinal: 1,
  kind: "initial" as const,
  origin: "recorded" as const,
  instruction: QUESTION,
  instructionDigest: "e".repeat(64),
  profileSnapshot: notebookProfileSnapshot,
  observedBase: null,
  editorSnapshot: null,
  snapshotClassification: "not_applicable" as const,
  unresolvedPaths: [],
  effectiveBaseVersion: null,
  manualVersion: null,
  revisionContext: null,
  hubRequestDigest: "f".repeat(64),
  catalystTraceId: "catalyst-trace-1",
  hubTraceId: "hub-trace-1",
  generationEvidenceRef: {
    evidenceId: "99999999-9999-4999-8999-999999999999",
    evidenceDigest: "1".repeat(64),
    detailPath:
      `/v1/catalyst/workbench/sessions/${notebookSession.sessionId}/turns/` +
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/generation-evidence",
  },
  recoveryReferences: null,
  status: "completed" as const,
  outputVersions: [
    {
      versionId: workbenchVersion.versionId,
      queryDigest: workbenchVersion.queryDigest,
      parentVersionId: null,
      role: "writer" as const,
      authorType: "model" as const,
      contractValid: true,
      validationId: workbenchValidation.validationId,
      selected: true,
    },
  ],
  selectedVersionId: workbenchVersion.versionId,
  resultingCurrentVersion: {
    versionId: workbenchVersion.versionId,
    queryDigest: workbenchVersion.queryDigest,
  },
  events: [],
  failure: null,
  createdAt: "2026-07-17T00:00:00Z",
  updatedAt: "2026-07-17T00:00:02Z",
};

const notebookTimeline = {
  contractVersion: "catalyst.workbench.turn.timeline.v1" as const,
  sessionId: notebookSession.sessionId,
  currentTurnId: initialNotebookTurn.turnId,
  currentVersion: initialNotebookTurn.resultingCurrentVersion,
  turns: [initialNotebookTurn],
};

const completedFollowupTurn = {
  ...initialNotebookTurn,
  turnId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  ordinal: 2,
  kind: "followup" as const,
  instruction: "Only include released results",
  instructionDigest: "2".repeat(64),
  observedBase: initialNotebookTurn.resultingCurrentVersion,
  snapshotClassification: "reused" as const,
  effectiveBaseVersion: initialNotebookTurn.resultingCurrentVersion,
  selectedVersionId: "33333333-3333-4333-8333-333333333333",
  resultingCurrentVersion: {
    versionId: "33333333-3333-4333-8333-333333333333",
    queryDigest: "3".repeat(64),
  },
  outputVersions: [
    {
      versionId: "33333333-3333-4333-8333-333333333333",
      queryDigest: "3".repeat(64),
      parentVersionId: workbenchVersion.versionId,
      role: "writer" as const,
      authorType: "model" as const,
      contractValid: true,
      validationId: "44444444-4444-4444-8444-444444444444",
      selected: true,
    },
  ],
};

// Creating with no question opens an empty session: named and grounded in a
// source, with nothing asked yet. Mirrors the Gateway so tests exercise the
// two-step flow the rail drives.
const EMPTY_SESSION_ID = "00000000-0000-4000-8000-000000000eee";

const emptySessionFields = {
  sessionId: EMPTY_SESSION_ID,
  question: "",
  currentVersionId: null,
  currentVersion: null,
  versions: [],
  validations: [],
  latestValidation: null,
  executions: [],
  draftSeed: null,
} as const;

const emptySessionFrom = (session: WorkbenchSession): WorkbenchSession => ({
  ...session,
  sessionId: EMPTY_SESSION_ID,
  question: "",
  currentVersionId: null,
  currentVersion: null,
  versions: [],
  validations: [],
  latestValidation: null,
  executions: [],
  draftSeed: null,
});

const emptyTimeline = {
  contractVersion: "catalyst.workbench.turn.timeline.v1",
  sessionId: EMPTY_SESSION_ID,
  currentTurnId: null,
  currentVersion: null,
  turns: [],
};

const makeNotebookApi = (
  session: WorkbenchSession = notebookSession,
  timeline: unknown = notebookTimeline,
) =>
  Object.assign(makeApi(), {
    getQueryOptions: vi.fn().mockResolvedValue(notebookQueryOptions),
    getWorkbenchCatalog: vi.fn().mockResolvedValue(editorCatalog),
    createWorkbenchSession: vi
      .fn()
      .mockImplementation((question: string) =>
        Promise.resolve(question.trim() ? session : emptySessionFrom(session)),
      ),
    askWorkbenchSessionQuestion: vi.fn().mockResolvedValue(session),
    getWorkbenchSession: vi
      .fn()
      .mockImplementation((sessionId: string) =>
        Promise.resolve(
          sessionId === EMPTY_SESSION_ID ? emptySessionFrom(session) : session,
        ),
      ),
    getWorkbenchTurns: vi
      .fn()
      .mockImplementation((sessionId: string) =>
        Promise.resolve(sessionId === EMPTY_SESSION_ID ? emptyTimeline : timeline),
      ),
    createWorkbenchTurn: vi.fn().mockResolvedValue(completedFollowupTurn),
    getWorkbenchGenerationEvidence: vi.fn(),
    createWorkbenchVersion: vi.fn(),
    executeWorkbenchVersion: vi.fn(),
  });

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
          qualifiedName: "analytics.lab_result_fact_v1",
          grain: "One row per FHIR Observation.",
          columns: [
            {
              name: "patient_id",
              logicalType: "string",
              nullable: false,
              description: "FHIR Patient resource identifier.",
            },
            {
              name: "result_value",
              logicalType: "decimal",
              nullable: true,
              description: "Numeric result value.",
            },
            {
              name: "test_name",
              logicalType: "string",
              nullable: true,
              description: "Laboratory test display name.",
            },
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
  // The workspace writes the selected source into the query string, so each
  // test has to start from a clean URL or it inherits the previous selection.
  window.history.replaceState(null, "", "/");
});

/**
 * The session control owns both the session list and the data source, so
 * reaching either means opening it the way a user does.
 */
const openSessionMenu = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(
    screen.getByRole("button", { name: /^Session:/ }),
  );
};

const openNewSessionForm = async (user: ReturnType<typeof userEvent.setup>) => {
  await openSessionMenu(user);
  await user.click(screen.getByRole("menuitem", { name: /New session/ }));
};

describe("Catalyst query workflow", () => {
  it("cannot generate before a question is written", () => {
    render(<App api={makeApi()} />);

    // This used to also assert the demo banner. The banner is gone -- it was
    // a label, not a safeguard. The safeguards are synthetic data, local
    // models, a read-only database identity and an explicit Run, none of
    // which this screen can bypass; the one it can demonstrate is that
    // nothing is generated until a question exists.
    expect(screen.getByRole("button", { name: "Generate query" })).toBeDisabled();
  });

  it("keeps the Ask OpenELIS input directly focusable", async () => {
    const user = userEvent.setup();
    render(<App api={makeApi()} />);

    await user.click(screen.getByRole("button", { name: "Ask a question" }));

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
    await user.click(screen.getByRole("button", { name: "Ask a question" }));
    expect(screen.getByRole("heading", { name: "Workbench" })).toHaveFocus();
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
        "Catalyst is generating an editable SQL draft with the selected profile.",
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

  it("shows the dataset browser and uses the Gateway-owned available profile", async () => {
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

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^DATA/ }));
    await user.click(
      await screen.findByText("Preview available laboratory records"),
    );
    expect(await screen.findByText("1,152")).toBeVisible();
    expect(screen.queryByText("Synthetic laboratory dataset")).not.toBeInTheDocument();
    expect(screen.queryByText("Example questions")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Show viral load results since 2026-01-01"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/test types and numeric distributions/i),
    ).not.toBeInTheDocument();
    // Source-neutral: the workbench is not laboratory-only, and this composer
    // is shown for whichever catalog the session is grounded in.
    expect(screen.getByLabelText("Question")).toHaveAttribute(
      "placeholder",
      "Describe the data you want to explore",
    );
    expect(screen.getByLabelText("Model profile")).toHaveValue(
      "catalyst-query-gemma-e4b",
    );
    const profileSelector = screen.getByLabelText("Model profile");
    /*
     * The option carries the profile's prose label only. It used to append the
     * model aliases, which restated what the label already said and overflowed
     * the control; the concrete aliases are disclosed in helper text beneath
     * the field instead, so they are still on screen (and still on camera in
     * the published demo cuts) without crowding the option.
     */
    expect(
      within(profileSelector).getByRole("option", {
        name: "Catalyst governed query — Gemma 4 E4B",
      }),
    ).toBeInTheDocument();
    expect(
      within(profileSelector).queryByRole("option", { name: /gemma-e4b/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("gemma-e4b")).toBeVisible();
    expect(
      within(profileSelector).getByRole("option", {
        name: "Split generation and review",
      }),
    ).toBeInTheDocument();
    expect(
      within(profileSelector).queryByRole("option", {
        name: "Offline research profile",
      }),
    ).not.toBeInTheDocument();

    // Record preview is a native disclosure inside the rail's DATA section;
    // it keeps its filter state across close and reopen.
    const records = screen
      .getByText("Preview available laboratory records")
      .closest("details")!;
    expect(records).toHaveAttribute("open");
    expect(screen.getByText("9000 copies/ml")).toBeVisible();
    const patientFilter = screen.getByLabelText("Patient FHIR ID");
    await user.type(patientFilter, "patient-123");
    await user.click(screen.getByText("Preview available laboratory records"));
    expect(records).not.toHaveAttribute("open");
    await user.click(screen.getByText("Preview available laboratory records"));
    expect(records).toHaveAttribute("open");
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
    const user = await askQuestion();

    expect(api.createWorkbenchSession).toHaveBeenCalledWith(
      QUESTION,
      "catalyst-query-gemma-e4b",
      undefined,
      undefined,
      undefined,
      undefined,
    );
    expect(api.submitQuestion).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("heading", { name: "New draft" }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toBeVisible();
    // Findings never block a run: the workbench stays usable while the
    // Details panel carries why the model output was rejected.
    expect(screen.getByRole("button", { name: "Run query" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /^Details/ }));
    const details = screen.getByRole("complementary", { name: "Details" });
    expect(within(details).getByText("policy.unit_not_grounded")).toBeVisible();
    expect(within(details).getByText(/validation is advisory/i)).toBeVisible();
  });

  it("clears only the editable draft while preserving the active session evidence", async () => {
    const api = makeApi();
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    const user = await askQuestion();
    expect(await screen.findByRole("heading", { name: "New draft" })).toBeVisible();
    expect(localStorage.getItem("catalyst.workbench.activeSessionId")).toBe(
      workbenchSession.sessionId,
    );

    await user.click(screen.getByRole("button", { name: "Clear draft" }));

    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(/^$/);
    expect(screen.queryByLabelText("Parameter 1 name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    // Clearing the editor does not clear the session: its identity is still
    // on the banner and its immutable versions are still in Details.
    expect(
      screen.getByText(`Session ${workbenchSession.sessionId.slice(0, 8)}`),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^Details/ }));
    await user.click(screen.getByRole("tab", { name: "Versions" }));
    expect(
      within(screen.getByRole("complementary", { name: "Details" })).getByText(
        "Version 1",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: /Run(ning)?( query)?/ })).toBeDisabled();
    expect(localStorage.getItem("catalyst.workbench.activeSessionId")).toBe(
      workbenchSession.sessionId,
    );
  });

  it("starts a clean browser session without deleting retained server evidence", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(queryOptions);
    api.createWorkbenchSession = vi
      .fn()
      .mockImplementation((question: string) =>
        Promise.resolve(
          question.trim()
            ? workbenchSession
            : { ...workbenchSession, ...emptySessionFields },
        ),
      );
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    const user = await askQuestion();
    expect(await screen.findByRole("heading", { name: "New draft" })).toBeVisible();

    await openNewSessionForm(user);
    await user.click(screen.getByRole("button", { name: "Start session" }));

    expect(screen.queryByRole("heading", { name: "New draft" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("");
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveFocus());
    expect(screen.getByLabelText("Model profile")).toHaveValue(
      "catalyst-query-gemma-e4b",
    );
    await waitFor(() =>
      expect(localStorage.getItem("catalyst.workbench.activeSessionId")).toBe(
        EMPTY_SESSION_ID,
      ),
    );
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("submits an unchanged editor as the exact current-version snapshot without a duplicate version", async () => {
    const api = makeNotebookApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toHaveValue(
      notebookQueryOptions.defaultProfileId,
    );
    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    expect(
      await screen.findByRole("heading", { name: /^Refine \[\d+\]$/ }),
    ).toBeVisible();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    expect(document.querySelectorAll("textarea:not([disabled])")).toHaveLength(1);
    expect(
      screen.getByText(/This query has not been executed.*without an execution summary/i),
    ).toBeVisible();
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Only include released results",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const [sessionId, request] = vi.mocked(api.createWorkbenchTurn).mock.calls[0]!;
    expect(sessionId).toBe(notebookSession.sessionId);
    expect(request).toEqual({
      contractVersion: "catalyst.workbench.turn.request.v1",
      instruction: "Only include released results",
      profileId: "catalyst-query-gemma-4-12b",
      observedBase: {
        versionId: workbenchVersion.versionId,
        queryDigest: workbenchVersion.queryDigest,
      },
      editorSnapshot: {
        contractVersion: "catalyst.workbench.editor-snapshot.v1",
        sql: asEditorText(workbenchVersion.sql),
        parameters: workbenchVersion.parameters,
        expectedColumns: workbenchVersion.expectedColumns,
        editorDigest: workbenchVersion.queryDigest,
      },
    });
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
    expect(api.executeWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("announces a generated successor and returns focus to the single SQL editor", async () => {
    const successorVersion = {
      ...workbenchVersion,
      versionId: completedFollowupTurn.resultingCurrentVersion.versionId,
      parentVersionId: workbenchVersion.versionId,
      ordinal: 2,
      sql: asEditorText(`${workbenchVersion.sql} AND result_status = 'final'`),
      queryDigest: completedFollowupTurn.resultingCurrentVersion.queryDigest,
      createdAt: "2026-07-17T00:00:03Z",
    } satisfies WorkbenchQueryVersion;
    const successorSession = {
      ...notebookSession,
      currentVersionId: successorVersion.versionId,
      currentVersion: successorVersion,
      versions: [workbenchVersion, successorVersion],
      updatedAt: successorVersion.createdAt,
    } satisfies WorkbenchSession;
    const successorTimeline = {
      ...notebookTimeline,
      currentTurnId: completedFollowupTurn.turnId,
      currentVersion: completedFollowupTurn.resultingCurrentVersion,
      turns: [initialNotebookTurn, completedFollowupTurn],
    };
    const api = makeNotebookApi();
    api.getWorkbenchSession = vi.fn().mockResolvedValue(successorSession);
    api.getWorkbenchTurns = vi.fn()
      .mockResolvedValueOnce(notebookTimeline)
      .mockResolvedValueOnce(successorTimeline);
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    const editor = await screen.findByRole("textbox", { name: "SQL query" });
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Only include released results",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    expect(
      await screen.findByText(
        "The next query is ready. The SQL editor now contains it.",
      ),
    ).toHaveAttribute("role", "status");
    await waitFor(() => expect(editor).toHaveFocus());
    showsQuery(editor, successorVersion.sql);
    expect(screen.getAllByRole("textbox", { name: "SQL query" })).toHaveLength(1);
  });

  it("submits a dirty contract-valid buffer exactly and lets the turn endpoint promote it once", async () => {
    const api = makeNotebookApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    expect(
      await screen.findByRole("heading", { name: /^Refine \[\d+\]$/ }),
    ).toBeVisible();

    const minimum = screen.getByLabelText("Parameter 2 value");
    await user.clear(minimum);
    await user.type(minimum, "2500");
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Keep the same shape and use the edited threshold",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const request = vi.mocked(api.createWorkbenchTurn).mock.calls[0]![1];
    expect(request.observedBase).toEqual({
      versionId: workbenchVersion.versionId,
      queryDigest: workbenchVersion.queryDigest,
    });
    expect(request.editorSnapshot).toEqual({
      contractVersion: "catalyst.workbench.editor-snapshot.v1",
      sql: asEditorText(workbenchVersion.sql),
      parameters: [
        workbenchVersion.parameters[0],
        {
          ...workbenchVersion.parameters[1],
          source: "human",
          value: 2500,
        },
      ],
      expectedColumns: workbenchVersion.expectedColumns,
      editorDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
    expect(request.editorSnapshot.editorDigest).not.toBe(
      workbenchVersion.queryDigest,
    );
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("keeps model expected columns when the query is only reformatted", async () => {
    const versionWithColumns = {
      ...workbenchVersion,
      expectedColumns: [
        { name: "count", logicalType: "integer" as const, nullable: false },
      ],
    };
    const sessionWithColumns = {
      ...notebookSession,
      currentVersion: versionWithColumns,
      versions: [versionWithColumns],
    };
    const api = makeNotebookApi(sessionWithColumns, {
      ...notebookTimeline,
      currentVersion: {
        versionId: versionWithColumns.versionId,
        queryDigest: versionWithColumns.queryDigest,
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    await screen.findByRole("textbox", { name: "SQL query" });
    await user.click(screen.getByRole("button", { name: "Format SQL" }));
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Keep the manually edited limit",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const request = vi.mocked(api.createWorkbenchTurn).mock.calls[0]![1];
    // Only the layout moved, so the model's declared columns still describe the
    // query. This test previously asserted the opposite: pressing Format SQL
    // dropped them, because formatting was indistinguishable from an edit.
    expect(request.editorSnapshot.sql).toContain("SELECT");
    expect(request.editorSnapshot.expectedColumns).toEqual(
      versionWithColumns.expectedColumns,
    );
  });

  it("clears model expected columns after a real SQL edit", async () => {
    const versionWithColumns = {
      ...workbenchVersion,
      expectedColumns: [
        { name: "count", logicalType: "integer" as const, nullable: false },
      ],
    };
    const sessionWithColumns = {
      ...notebookSession,
      currentVersion: versionWithColumns,
      versions: [versionWithColumns],
    };
    const api = makeNotebookApi(sessionWithColumns, {
      ...notebookTimeline,
      currentVersion: {
        versionId: versionWithColumns.versionId,
        queryDigest: versionWithColumns.queryDigest,
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    const editor = await screen.findByRole("textbox", { name: "SQL query" });
    // A genuine change to the query, not a reflow of it.
    await user.click(editor);
    await user.keyboard(" AND 1 = 1");
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Keep the manually edited limit",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const request = vi.mocked(api.createWorkbenchTurn).mock.calls[0]![1];
    expect(request.editorSnapshot.expectedColumns).toEqual([]);
    expect(request.editorSnapshot.editorDigest).not.toBe(
      versionWithColumns.queryDigest,
    );
  });

  it("freezes the exact editor snapshot and session actions during follow-up generation", async () => {
    const api = makeNotebookApi();
    api.createWorkbenchTurn = vi.fn().mockImplementation(
      () => new Promise(() => undefined),
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    await user.type(
      await screen.findByRole("textbox", { name: "Follow-up instruction" }),
      "Only include released results",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveAttribute(
      "contenteditable",
      "false",
    );
    expect(screen.getByLabelText("Parameter 1 value")).toBeDisabled();
    await openNewSessionForm(user);
    expect(screen.getByRole("button", { name: "Start session" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Clear draft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Run(ning)?( query)?/ })).toBeDisabled();
  });

  it("disables and guards refinement while a run is saving its version", async () => {
    const api = makeNotebookApi();
    api.createWorkbenchVersion = vi.fn().mockImplementation(
      () => new Promise(() => undefined),
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    const instruction = await screen.findByRole("textbox", {
      name: "Follow-up instruction",
    });
    await user.type(instruction, "Only include released results");
    // An edit, so the run really does have a version to save — an unchanged
    // draft runs the version it came from and saves nothing.
    await user.click(screen.getByRole("textbox", { name: "SQL query" }));
    await user.keyboard("{Control>}{End}{/Control}");
    await user.paste(" AND 1 = 1");
    await user.click(screen.getByRole("button", { name: "Run query" }));

    await waitFor(() => expect(api.createWorkbenchVersion).toHaveBeenCalledOnce());
    expect(instruction).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Model profile" })).toBeDisabled();
    const generate = screen.getByRole("button", {
      name: "Generate next query",
    });
    expect(generate).toBeDisabled();
    fireEvent.submit(generate.closest("form")!);
    expect(api.createWorkbenchTurn).not.toHaveBeenCalled();
  });

  it("sends a nonempty unresolved buffer for correction without first saving it as a version", async () => {
    const unresolvedNotebookSession = {
      ...unresolvedRawSession,
      profileId: "catalyst-query-gemma-4-12b",
    } satisfies WorkbenchSession;
    const unresolvedTimeline = {
      ...notebookTimeline,
      sessionId: unresolvedNotebookSession.sessionId,
      currentVersion: null,
      turns: [
        {
          ...initialNotebookTurn,
          sessionId: unresolvedNotebookSession.sessionId,
          status: "failed" as const,
          selectedVersionId: null,
          resultingCurrentVersion: null,
          outputVersions: [],
          failure: {
            stage: "writer_output_contract",
            code: "writer_output_contract_failed",
            message: "The generated parameter structure was unresolved.",
          },
        },
      ],
    };
    const api = makeNotebookApi(unresolvedNotebookSession, unresolvedTimeline);
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    expect(await screen.findByText("Unresolved model draft")).toBeVisible();

    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Repair the missing parameter names",
    );
    const generate = screen.getByRole("button", {
      name: "Generate next query",
    });
    expect(generate).toBeEnabled();
    await user.click(generate);

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const request = vi.mocked(api.createWorkbenchTurn).mock.calls[0]![1];
    expect(request.observedBase).toBeNull();
    expect(request.editorSnapshot).toEqual({
      contractVersion: "catalyst.workbench.editor-snapshot.v1",
      sql: asEditorText(unresolvedRawSql),
      parameters: unresolvedRawSession.draftSeed!.parameters,
      expectedColumns: [],
      editorDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("disables an empty follow-up and restores the current query locally without a version", async () => {
    const api = makeNotebookApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    expect(
      await screen.findByRole("heading", { name: /^Refine \[\d+\]$/ }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Clear draft" }));
    expect(
      screen.getByRole("button", { name: "Generate next query" }),
    ).toBeDisabled();
    expect(api.createWorkbenchTurn).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Restore the current query" }));
    showsQuery(
      screen.getByRole("textbox", { name: "SQL query" }),
      workbenchVersion.sql,
    );
    expect(screen.getByLabelText("Parameter 2 value")).toHaveValue("1000");
    expect(
      screen.getByRole("button", { name: "Generate next query" }),
    ).toBeEnabled();
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
  });

  it("restores the notebook after refresh and New Session removes every prior turn context", async () => {
    const api = makeNotebookApi();
    localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      notebookSession.sessionId,
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    await waitFor(() =>
      expect(api.getWorkbenchSession).toHaveBeenCalledWith(
        notebookSession.sessionId,
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(api.getWorkbenchTurns).toHaveBeenCalledWith(
        notebookSession.sessionId,
        expect.any(AbortSignal),
      ),
    );
    expect(
      (await screen.findAllByText(notebookSession.question)).length,
    ).toBeGreaterThan(0);
    // The restored thread is addressable: turn 1 is its own cell, expanded
    // because it is the newest, and anchored so it can be deep-linked.
    expect(
      screen.getByRole("button", { name: /query turn 1/i }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("turn-1")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /^Refine \[\d+\]$/ }),
    ).toBeVisible();
    showsQuery(
      screen.getByRole("textbox", { name: "SQL query" }),
      workbenchVersion.sql,
    );
    expect(screen.getByRole("combobox", { name: "Model profile" })).toHaveValue(
      notebookSession.profileId,
    );
    expect(api.createWorkbenchSession).not.toHaveBeenCalled();
    expect(api.createWorkbenchTurn).not.toHaveBeenCalled();

    await openNewSessionForm(user);
    await user.click(screen.getByRole("button", { name: "Start session" }));
    expect(screen.queryByRole("button", { name: /query turn 1/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Follow-up instruction" }))
      .not.toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("");
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveFocus());
    // The new session is real and remembered, but nothing is in it yet.
    await waitFor(() =>
      expect(localStorage.getItem("catalyst.workbench.activeSessionId")).toBe(
        EMPTY_SESSION_ID,
      ),
    );

    await user.type(screen.getByLabelText("Question"), "Count creatinine results");
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    // The question seeds the session it was asked in, rather than opening a
    // second one: its source was already settled when it was created.
    await waitFor(() =>
      expect(api.askWorkbenchSessionQuestion).toHaveBeenCalledWith(
        EMPTY_SESSION_ID,
        "Count creatinine results",
        "catalyst-query-gemma-4-12b",
      ),
    );
    expect(api.createWorkbenchTurn).not.toHaveBeenCalled();
  });

  it("submits the advertised available profile after leaving a session with a retired profile", async () => {
    const retiredSession = {
      ...notebookSession,
      profileId: "catalyst-query-retired",
      provenance: {
        ...notebookSession.provenance,
        profileSnapshot: {
          profileId: "catalyst-query-retired",
          roleModels: {
            query_generate: "retired-writer",
            query_review: "retired-reviewer",
          },
        },
      },
    } satisfies WorkbenchSession;
    const currentOptions = {
      ...queryOptions,
      profiles: [queryOptions.profiles[0]!],
    };
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue(currentOptions);
    api.getWorkbenchSession = vi.fn().mockResolvedValue(retiredSession);
    api.askWorkbenchSessionQuestion = vi.fn().mockResolvedValue(workbenchSession);
    api.getWorkbenchTurns = vi
      .fn()
      .mockImplementation((sessionId: string) =>
        Promise.resolve(
          sessionId === EMPTY_SESSION_ID ? emptyTimeline : notebookTimeline,
        ),
      );
    api.createWorkbenchSession = vi
      .fn()
      .mockImplementation((question: string) =>
        Promise.resolve(
          question.trim()
            ? workbenchSession
            : { ...workbenchSession, ...emptySessionFields },
        ),
      );
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn();
    localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      retiredSession.sessionId,
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(
      await screen.findByRole("heading", { name: "New draft" }),
    ).toBeVisible();
    await openNewSessionForm(user);
    await user.click(screen.getByRole("button", { name: "Start session" }));

    // The retired profile does not follow the analyst into the new session:
    // the question is asked with one the Gateway still advertises.
    const profileSelector = await screen.findByRole("combobox", {
      name: "Model profile",
    });
    expect(profileSelector).toHaveValue("catalyst-query-gemma-e4b");
    await user.type(screen.getByLabelText("Question"), "Count recent results");
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    await waitFor(() =>
      expect(api.askWorkbenchSessionQuestion).toHaveBeenCalledWith(
        EMPTY_SESSION_ID,
        "Count recent results",
        "catalyst-query-gemma-e4b",
      ),
    );
  });

  it("uses an available revision profile after restoring a legacy session without rewriting its turn snapshot", async () => {
    const legacySession = {
      ...notebookSession,
      profileId: "catalyst-query-gemma-e4b",
    } satisfies WorkbenchSession;
    const legacyTimeline = {
      ...notebookTimeline,
      turns: [
        {
          ...initialNotebookTurn,
          profileSnapshot: {
            ...notebookProfileSnapshot,
            profileId: "catalyst-query-gemma-e4b",
            profileName: "Legacy same-family profile",
            writer: { modelId: "gemma-e4b" },
            reviewer: { modelId: "gemma-e4b" },
          },
        },
      ],
    };
    const api = makeNotebookApi(legacySession, legacyTimeline);
    localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      legacySession.sessionId,
    );
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(
      await screen.findByText(/Generated by Legacy same-family profile/),
    ).toBeVisible();
    const profileSelector = screen.getByRole("combobox", {
      name: "Model profile",
    });
    await waitFor(() =>
      expect(profileSelector).toHaveValue("catalyst-query-gemma-4-12b"),
    );
    expect(
      within(profileSelector).queryByRole("option", {
        name: /Catalyst governed query/i,
      }),
    ).not.toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Only include released results",
    );
    await user.click(
      screen.getByRole("button", { name: "Generate next query" }),
    );

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    expect(vi.mocked(api.createWorkbenchTurn).mock.calls[0]![1].profileId).toBe(
      "catalyst-query-gemma-4-12b",
    );
    expect(
      screen.getByText(/Generated by Legacy same-family profile/),
    ).toBeVisible();
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
    showsQuery(
      screen.getByRole("textbox", { name: "SQL query" }),
      unresolvedRawSql,
    );
    expect(screen.getByLabelText("Parameter 1 name")).toHaveValue("");
    expect(screen.getByLabelText("Parameter 1 value")).toHaveValue("Viral Load");
    expect(screen.getByLabelText("Parameter 2 name")).toHaveValue("");
    expect(screen.getByLabelText("Parameter 2 value")).toHaveValue("1000");
    await user.type(screen.getByLabelText("Parameter 1 name"), "test_name");
    await user.type(screen.getByLabelText("Parameter 2 name"), "threshold");
    await user.click(screen.getByRole("button", { name: "Run query" }));

    await waitFor(() =>
      expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
        unresolvedRawSession.sessionId,
        {
          sql: asEditorText(unresolvedRawSql),
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
    // The editor is CodeMirror and mounts in an effect, so it can lag the
    // notification by a tick -- fast enough to look synchronous on a laptop
    // and not on a CI runner. Await the mount rather than assume it.
    showsQuery(
      await screen.findByRole("textbox", { name: "SQL query" }),
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

    showsQuery(
      await screen.findByRole("textbox", { name: "SQL query" }),
      workbenchVersion.sql,
    );
    expect(screen.queryByText("Unresolved model draft")).not.toBeInTheDocument();
  });

  it("persists a manually corrected parameter as its own version when run", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    const child = childWorkbenchSession("warning");
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn().mockResolvedValue(child);
    api.executeWorkbenchVersion = vi.fn().mockResolvedValue(
      failedWorkbenchExecution,
    );
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    const parameterName = await screen.findByLabelText("Parameter 2 name");
    await user.clear(parameterName);
    await user.type(parameterName, "threshold");
    await user.click(screen.getByRole("button", { name: "Run query" }));

    await waitFor(() =>
      expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
        workbenchSession.sessionId,
        {
          parentVersionId: workbenchVersion.versionId,
          parentQueryDigest: workbenchVersion.queryDigest,
          sql: asEditorText(workbenchVersion.sql),
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
    // One button, one intent: the corrected draft is saved as its own version
    // and that exact version is what runs.
    expect(api.executeWorkbenchVersion).toHaveBeenCalledWith(
      child.currentVersion!.versionId,
      child.currentVersion!.queryDigest,
      expect.any(String),
    );
  });

  it("runs the model's own version when the editor has not changed it", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    api.createWorkbenchSession = vi.fn().mockResolvedValue(workbenchSession);
    api.createWorkbenchVersion = vi.fn();
    api.executeWorkbenchVersion = vi.fn().mockResolvedValue({
      ...failedWorkbenchExecution,
      queryDigest: workbenchVersion.queryDigest,
    });
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    await user.click(await screen.findByRole("button", { name: "Run query" }));

    // Running a query nobody rewrote is not authoring one. Every version this
    // endpoint creates is recorded as hand-authored by the gateway, so saving
    // one here labelled the model's own query "Edited by hand" the moment it
    // was run -- which is what Ian saw, and what the editor now presenting SQL
    // formatted would otherwise have made unavoidable.
    await waitFor(() =>
      expect(api.executeWorkbenchVersion).toHaveBeenCalledWith(
        workbenchVersion.versionId,
        workbenchVersion.queryDigest,
        expect.any(String),
      ),
    );
    expect(api.createWorkbenchVersion).not.toHaveBeenCalled();
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
    const editor = await screen.findByRole("textbox", { name: "SQL query" });
    // A real edit, so there genuinely is a new version to persist: an
    // unchanged draft now runs the version it came from.
    await user.click(editor);
    await user.keyboard("{Control>}{End}{/Control}");
    await user.paste(" LIMIT 5");
    await user.click(await screen.findByRole("button", { name: "Run query" }));

    expect(api.createWorkbenchVersion).toHaveBeenCalledWith(
      workbenchSession.sessionId,
      {
        parentVersionId: workbenchVersion.versionId,
        parentQueryDigest: workbenchVersion.queryDigest,
        sql: `${asEditorText(workbenchVersion.sql)} LIMIT 5`,
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
      await screen.findByRole("heading", { name: "New draft" }),
    ).toBeVisible();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveTextContent(
      "COUNT(DISTINCT patient_id)",
    );
    resolveQueryOptions(queryOptions);
    await waitFor(() => expect(api.getQueryOptions).toHaveBeenCalled());
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

  it("blocks generation when runtime discovery marks every profile unavailable", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue({
      ...queryOptions,
      profiles: queryOptions.profiles.map((profile) => ({
        ...profile,
        available: false,
        unavailableReasons: ["model backend is unavailable"],
      })),
    });
    render(<App api={api} />);

    expect(
      await screen.findByText(
        "No configured model profile is currently available.",
      ),
    ).toBeVisible();
    expect(screen.queryByLabelText("Model profile")).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Question"), QUESTION);
    expect(screen.getByRole("button", { name: "Generate query" })).toBeDisabled();
    expect(api.submitQuestion).not.toHaveBeenCalled();
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
    expect(screen.getByLabelText("Question")).toBeEnabled();
    expect(screen.getByLabelText("Model profile")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Generate query" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Ask a question" }));
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

  it("keeps the selected data source visible when only one source is registered", async () => {
    const api = makeNotebookApi();
    api.getDataSources = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.data-sources.v1",
      defaultDataSourceId: "openelis",
      dataSources: [
        { id: "openelis", label: "OpenELIS Laboratory", available: true },
      ],
    });
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    // One registered source is nothing to choose between, so it is reported
    // in the rail rather than offered as a switch.
    expect(screen.queryByLabelText("Data source")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("complementary", { name: "Catalyst" })).getByText(
        "OpenELIS Laboratory",
      ),
    ).toBeVisible();
  });

  it("offers the source only when creating a session, and filters unavailable ones", async () => {
    const api = makeNotebookApi();
    api.getDataSources = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.data-sources.v1",
      defaultDataSourceId: "openelis",
      dataSources: [
        { id: "openelis", label: "OpenELIS Laboratory", available: true },
        { id: "openmrs-hiv", label: "OpenMRS HIV/ART program", available: true },
        { id: "not-yet-provisioned", label: "Not yet provisioned", available: false },
      ],
    });
    render(<App api={api} />);
    const user = userEvent.setup();

    await openNewSessionForm(user);
    const switcher = await screen.findByLabelText("Data source");
    expect(switcher).toHaveValue("openelis");
    expect(
      within(switcher).queryByRole("option", { name: "Not yet provisioned" }),
    ).not.toBeInTheDocument();
    expect(
      within(switcher).getByRole("option", { name: "OpenMRS HIV/ART program" }),
    ).toBeInTheDocument();
  });

  const twoSourceApi = () => {
    const api = makeNotebookApi();
    api.getDataSources = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.data-sources.v1",
      defaultDataSourceId: "openelis",
      dataSources: [
        { id: "openelis", label: "OpenELIS Laboratory", available: true },
        { id: "openmrs-hiv", label: "OpenMRS HIV/ART program", available: true },
      ],
    });
    return api;
  };

  it("publishes the selected source to the URL so the view can be reloaded or shared", async () => {
    const api = twoSourceApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    await openNewSessionForm(user);
    const switcher = await screen.findByLabelText("Data source");
    await user.selectOptions(switcher, "openmrs-hiv");

    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("dataSource")).toBe(
        "openmrs-hiv",
      ),
    );
  });

  it("restores the source named in the URL instead of the registered default", async () => {
    window.history.replaceState(null, "", "/?dataSource=openmrs-hiv");
    const api = twoSourceApi();
    render(<App api={api} />);
    const user = userEvent.setup();

    await openNewSessionForm(user);
    expect(await screen.findByLabelText("Data source")).toHaveValue("openmrs-hiv");
    await waitFor(() =>
      expect(api.getWorkbenchCatalog).toHaveBeenCalledWith(
        "openmrs-hiv",
        expect.anything(),
      ),
    );
    expect(api.getWorkbenchCatalog).not.toHaveBeenCalledWith(
      "openelis",
      expect.anything(),
    );
  });

  it("falls back to the default when the URL names a source this deployment does not serve", async () => {
    window.history.replaceState(null, "", "/?dataSource=retired-source");
    const api = twoSourceApi();
    render(<App api={api} />);
    const user = userEvent.setup();

    await openNewSessionForm(user);
    expect(await screen.findByLabelText("Data source")).toHaveValue("openelis");
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("dataSource")).toBe(
        "openelis",
      ),
    );
  });

  it("carries the session's chosen source into its turns and catalog fetches", async () => {
    const api = makeNotebookApi();
    api.getDataSources = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.data-sources.v1",
      defaultDataSourceId: "openelis",
      dataSources: [
        { id: "openelis", label: "OpenELIS Laboratory", available: true },
        { id: "openmrs-hiv", label: "OpenMRS HIV/ART program", available: true },
      ],
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await openNewSessionForm(user);
    const switcher = await screen.findByLabelText("Data source");
    await user.selectOptions(switcher, "openmrs-hiv");

    await waitFor(() =>
      expect(api.getWorkbenchCatalog).toHaveBeenCalledWith(
        "openmrs-hiv",
        expect.anything(),
      ),
    );

    await user.type(screen.getByLabelText("Question"), QUESTION);
    await user.click(screen.getByRole("button", { name: "Generate query" }));
    await waitFor(() => expect(api.createWorkbenchSession).toHaveBeenCalledOnce());
    expect(api.createWorkbenchSession).toHaveBeenCalledWith(
      QUESTION,
      "catalyst-query-gemma-4-12b",
      undefined,
      "openmrs-hiv",
      undefined,
      undefined,
    );

    await screen.findByRole("heading", { name: /^Refine \[\d+\]$/ });
    await user.type(
      screen.getByRole("textbox", { name: "Follow-up instruction" }),
      "Only include released results",
    );
    await user.click(screen.getByRole("button", { name: "Generate next query" }));

    await waitFor(() => expect(api.createWorkbenchTurn).toHaveBeenCalledOnce());
    const [, request] = vi.mocked(api.createWorkbenchTurn).mock.calls[0]!;
    expect(request).toMatchObject({ dataSourceId: "openmrs-hiv" });
  });

  it("stays functional when the data-sources endpoint is unavailable", async () => {
    const api = makeNotebookApi();
    api.getDataSources = vi.fn().mockRejectedValue(new Error("not found"));
    render(<App api={api} />);

    expect(await screen.findByLabelText("Model profile")).toBeEnabled();
    expect(screen.queryByLabelText("Data source")).not.toBeInTheDocument();
  });
});
