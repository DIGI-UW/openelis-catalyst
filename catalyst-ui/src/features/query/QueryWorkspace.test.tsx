import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryWorkspace } from "./QueryWorkspace";
import type { CatalystApi } from "./api";
import type {
  WorkbenchQueryVersion,
  WorkbenchSession,
  WorkbenchTurnTimeline,
} from "./types";

const version: WorkbenchQueryVersion = {
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId: "11111111-1111-4111-8111-111111111111",
  sessionId: "22222222-2222-4222-8222-222222222222",
  parentVersionId: null,
  ordinal: 1,
  authorType: "model",
  sql: "SELECT test_name, count(*) FROM analytics.lab_result_fact_v1 GROUP BY test_name",
  parameters: [],
  expectedColumns: [],
  queryDigest: "a".repeat(64),
  provenance: { model: "gemma-4-12b" },
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-08-06T00:00:01Z",
};

const session: WorkbenchSession = {
  contractVersion: "catalyst.workbench.session.v1",
  sessionId: version.sessionId,
  question: "Count results by test",
  profileId: "catalyst-query",
  datasetId: "openelis",
  datasetVersion: "run-1",
  catalogVersion: "catalog-1",
  currentVersionId: version.versionId,
  browserState: {},
  provenance: {},
  status: "active",
  createdAt: "2026-08-06T00:00:00Z",
  updatedAt: "2026-08-06T00:00:01Z",
  versions: [version],
  currentVersion: version,
  validations: [],
  latestValidation: null,
  executions: [],
};

const timeline: WorkbenchTurnTimeline = {
  contractVersion: "catalyst.workbench.turn.timeline.v1",
  sessionId: session.sessionId,
  currentTurnId: "33333333-3333-4333-8333-333333333333",
  currentVersion: {
    versionId: version.versionId,
    queryDigest: version.queryDigest,
  },
  turns: [
    {
      contractVersion: "catalyst.workbench.turn.v1",
      sessionId: session.sessionId,
      turnId: "33333333-3333-4333-8333-333333333333",
      ordinal: 1,
      kind: "initial",
      origin: "recorded",
      instruction: session.question,
      instructionDigest: "b".repeat(64),
      profileSnapshot: {
        profileId: "catalyst-query",
        profileName: "Catalyst query",
        profileDigest: "c".repeat(64),
        writer: { modelId: "gemma-4-12b" },
        reviewer: { modelId: "qwen2.5-14b" },
        omissions: [],
      },
      observedBase: null,
      editorSnapshot: null,
      snapshotClassification: "not_applicable",
      unresolvedPaths: [],
      effectiveBaseVersion: null,
      manualVersion: null,
      revisionContext: null,
      hubRequestDigest: "d".repeat(64),
      catalystTraceId: "trace-1",
      hubTraceId: "hub-1",
      generationEvidenceRef: {
        evidenceId: "44444444-4444-4444-8444-444444444444",
        evidenceDigest: "e".repeat(64),
        detailPath: "/generation-evidence",
      },
      recoveryReferences: null,
      status: "completed",
      outputVersions: [
        {
          versionId: version.versionId,
          queryDigest: version.queryDigest,
          parentVersionId: null,
          role: "writer",
          authorType: "model",
          contractValid: true,
          validationId: null,
          selected: true,
        },
      ],
      selectedVersionId: version.versionId,
      resultingCurrentVersion: {
        versionId: version.versionId,
        queryDigest: version.queryDigest,
      },
      events: [],
      failure: null,
      createdAt: "2026-08-06T00:00:00Z",
      updatedAt: "2026-08-06T00:00:01Z",
    },
  ],
};

const api = (): CatalystApi => ({
  submitQuestion: vi.fn(),
  executePreview: vi.fn(),
  pollExecution: vi.fn(),
  getQueryOptions: vi.fn().mockResolvedValue({
    contractVersion: "catalyst.query-options.v1",
    defaultProfileId: "catalyst-query",
    profiles: [
      {
        id: "catalyst-query",
        label: "Catalyst query",
        available: true,
        revisionCapable: true,
        requiredModels: ["gemma-4-12b", "qwen2.5-14b"],
        roleModels: {
          query_generate: "gemma-4-12b",
          query_review: "qwen2.5-14b",
        },
        stages: ["query_generate", "query_review"],
        unavailableReasons: [],
      },
    ],
  }),
  getDatasetOverview: vi.fn().mockResolvedValue({
    contractVersion: "catalyst.dataset-overview.v1",
    datasetId: "openelis",
    synthetic: true,
    patients: 0,
    results: 0,
    testTypes: 0,
    firstObservedAt: null,
    lastObservedAt: null,
    tests: [],
    exampleQuestions: [],
  }),
  getDatasetRows: vi.fn().mockResolvedValue({
    contractVersion: "catalyst.dataset-rows.v1",
    total: 0,
    limit: 25,
    offset: 0,
    rows: [],
  }),
  createWorkbenchSession: vi.fn().mockResolvedValue(session),
  getWorkbenchSession: vi.fn().mockResolvedValue(session),
  createWorkbenchVersion: vi.fn(),
  executeWorkbenchVersion: vi.fn(),
  createWorkbenchTurn: vi.fn(),
  getWorkbenchTurns: vi.fn().mockResolvedValue(timeline),
});

describe("Dashboard Builder Ask shell", () => {
  it("makes the four product sections and compact data catalog available without example prompts", async () => {
    render(<QueryWorkspace api={api()} />);

    const navigation = screen.getByRole("navigation", { name: "Catalyst" });
    for (const name of ["Workbench", "Datasets", "Widgets", "Dashboards"]) {
      expect(within(navigation).getByRole("button", { name: new RegExp(`^${name}`) })).toBeVisible();
    }
    expect(screen.getByText(/^Available data ·/i)).toBeVisible();
    expect(screen.queryByText(/example questions/i)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveFocus());
  });

  it("keeps one active SQL editor, one New session action, and a fixed refinement composer", async () => {
    const client = api();
    const user = userEvent.setup();
    render(<QueryWorkspace api={client} />);

    await user.type(screen.getByLabelText("Question"), session.question);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    expect(await screen.findByRole("textbox", { name: "SQL query" })).toBeVisible();
    expect(screen.getAllByRole("textbox", { name: "SQL query" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "New session" })).toHaveLength(1);
    const composer = screen.getByRole("region", { name: /refine query v1/i });
    expect(composer).toHaveClass("turn-composer");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "SQL query" })).toBeVisible());
  });
});
