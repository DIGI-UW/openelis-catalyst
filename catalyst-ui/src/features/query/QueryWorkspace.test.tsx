import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryWorkspace } from "./QueryWorkspace";
import type { CatalystApi } from "./api";
import type {
  WorkbenchExecution,
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
  name: "Count results by test",
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
  createWorkbenchSession: vi
    .fn()
    .mockImplementation((question: string) =>
      Promise.resolve(
        question.trim()
          ? session
          : {
              ...session,
              sessionId: "empty-session",
              question: "",
              currentVersionId: null,
              currentVersion: null,
              versions: [],
              validations: [],
              latestValidation: null,
              executions: [],
              draftSeed: null,
            },
      ),
    ),
  askWorkbenchSessionQuestion: vi.fn().mockResolvedValue(session),
  getWorkbenchSession: vi.fn().mockResolvedValue(session),
  createWorkbenchVersion: vi.fn(),
  executeWorkbenchVersion: vi.fn(),
  createWorkbenchTurn: vi.fn(),
  getWorkbenchTurns: vi
    .fn()
    .mockImplementation((sessionId: string) =>
      Promise.resolve(
        sessionId === "empty-session"
          ? {
              contractVersion: "catalyst.workbench.turn.timeline.v1",
              sessionId: "empty-session",
              currentTurnId: null,
              currentVersion: null,
              turns: [],
            }
          : timeline,
      ),
    ),
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


describe("Dashboard Builder Ask shell", () => {
  it("puts sections, the session and the catalog in one resizable rail", async () => {
    const user = userEvent.setup();
    render(<QueryWorkspace api={api()} />);

    const rail = screen.getByRole("complementary", { name: "Catalyst" });
    const sections = within(rail).getByRole("navigation", { name: "Sections" });
    for (const name of ["Ask", "Datasets", "Widgets", "Dashboards"]) {
      expect(within(sections).getByRole("button", { name })).toBeVisible();
    }
    expect(screen.queryByText(/example questions/i)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Question")).toHaveFocus());

    // DATA and TURNS are mutually exclusive: whichever is open owns the
    // rail's free height, so neither can paint over the section nav.
    const data = within(rail).getByRole("button", { name: /^DATA/ });
    const turns = within(rail).getByRole("button", { name: /^TURNS/ });
    expect(turns).toHaveAttribute("aria-expanded", "true");
    expect(data).toHaveAttribute("aria-expanded", "false");

    await user.click(data);
    expect(data).toHaveAttribute("aria-expanded", "true");
    expect(turns).toHaveAttribute("aria-expanded", "false");
    expect(await within(rail).findByLabelText("Filter columns")).toBeVisible();

    // Closing the open section falls back to the thread, never to an empty rail.
    await user.click(data);
    expect(turns).toHaveAttribute("aria-expanded", "true");
  });

  it("resizes the rail from the keyboard and clamps it to the viewport", async () => {
    const user = userEvent.setup();
    render(<QueryWorkspace api={api()} />);

    const handle = screen.getByRole("separator", { name: "Resize sidebar" });
    expect(handle).toHaveAttribute("aria-valuenow", "240");

    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect(handle).toHaveAttribute("aria-valuenow", "272");

    // 200px is the floor however far left it is dragged.
    for (let index = 0; index < 5; index += 1) {
      await user.keyboard("{ArrowLeft}");
    }
    expect(handle).toHaveAttribute("aria-valuenow", "200");
  });

  it("keeps one active SQL editor and puts session management in the rail", async () => {
    const client = api();
    const user = userEvent.setup();
    render(<QueryWorkspace api={client} />);

    await user.type(screen.getByLabelText("Question"), session.question);
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    expect(await screen.findByRole("textbox", { name: "SQL query" })).toBeVisible();
    expect(screen.getAllByRole("textbox", { name: "SQL query" })).toHaveLength(1);
    // Session management lives in one place: the rail's session control.
    expect(screen.queryByRole("button", { name: "New session" })).not.toBeInTheDocument();
    await openSessionMenu(user);
    expect(
      screen.getAllByRole("menuitem", { name: /New session/ }),
    ).toHaveLength(1);
    const composer = screen.getByRole("region", { name: /refine query v1/i });
    expect(composer).toHaveClass("turn-composer");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "SQL query" })).toBeVisible());
  });

  it("grounds a restored session on its own source, not a conflicting URL", async () => {
    const client = api();
    // Resolve the source list after the session restore, so the invariant is
    // pinned under the ordering where the lookup gets the last word.
    client.getDataSources = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () =>
              resolve({
                contractVersion: "catalyst.data-sources.v1",
                defaultDataSourceId: "openelis-analytics",
                dataSources: [
                  {
                    id: "openelis-analytics",
                    label: "OpenELIS laboratory",
                    available: true,
                  },
                  { id: "openmrs-hiv", label: "OpenMRS HIV/ART", available: true },
                ],
              }),
            20,
          );
        }),
    );
    client.getWorkbenchSession = vi
      .fn()
      .mockResolvedValue({ ...session, dataSourceId: "openelis-analytics" });
    client.getWorkbenchCatalog = vi.fn().mockResolvedValue(null);
    window.history.replaceState({}, "", "/?dataSource=openmrs-hiv");
    window.localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      session.sessionId,
    );

    render(<QueryWorkspace api={client} />);

    // A session is grounded in one catalog, so a pasted or stale
    // `?dataSource=` must not retarget it — the catalog it reads, the URL it
    // advertises and the source its next turn targets all follow the session.
    const rail = await screen.findByRole("complementary", { name: "Catalyst" });
    await waitFor(() =>
      expect(within(rail).getByText("OpenELIS laboratory")).toBeVisible(),
    );
    await waitFor(() =>
      expect(new URL(window.location.href).searchParams.get("dataSource")).toBe(
        "openelis-analytics",
      ),
    );
    await waitFor(() =>
      expect(client.getWorkbenchCatalog).toHaveBeenLastCalledWith(
        "openelis-analytics",
        expect.any(AbortSignal),
      ),
    );
  });

  it("keeps the data source with the session, not with the model profile", async () => {
    const client = api();
    client.getDataSources = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.data-sources.v1",
      defaultDataSourceId: "openelis-analytics",
      dataSources: [
        { id: "openelis-analytics", label: "OpenELIS laboratory", available: true },
        { id: "openmrs-hiv", label: "OpenMRS HIV/ART", available: true },
      ],
    });
    client.listWorkbenchSessions = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.workbench.session-list.v1",
      sessions: [
        {
          sessionId: "older-session",
          name: "Turnaround time, Q2",
          question: "How long do results take?",
          dataSourceId: "openmrs-hiv",
          turnCount: 5,
          createdAt: "2026-08-01T00:00:00Z",
          updatedAt: "2026-08-01T00:00:00Z",
        },
      ],
    });
    const user = userEvent.setup();
    render(<QueryWorkspace api={client} />);

    const rail = screen.getByRole("complementary", { name: "Catalyst" });
    // The source a question will target is readable without opening anything.
    await waitFor(() =>
      expect(within(rail).getByText("OpenELIS laboratory")).toBeVisible(),
    );
    // It is a session property, so it is not offered beside the model
    // profile, which is a per-turn choice with a different lifetime.
    expect(screen.queryByLabelText("Data source")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Model profile")).toBeVisible();

    await user.click(within(rail).getByRole("button", { name: /^Session:/ }));
    expect(
      screen.getByRole("menuitem", { name: /Turnaround time, Q2/ }),
    ).toHaveTextContent("OpenMRS HIV/ART · 5 turns");

    await user.click(screen.getByRole("menuitem", { name: /New session/ }));
    expect(
      screen.getByText(/A session is grounded in one catalog/),
    ).toBeVisible();
    await user.selectOptions(
      screen.getByLabelText("Data source"),
      "openmrs-hiv",
    );
    await user.type(screen.getByLabelText("Name"), "CD4 cohort review");
    await user.click(screen.getByRole("button", { name: "Start session" }));

    await waitFor(() =>
      expect(within(rail).getByText("OpenMRS HIV/ART")).toBeVisible(),
    );

    // The name and source chosen in the rail are what the session is opened
    // with, before any question exists.
    await waitFor(() =>
      expect(client.createWorkbenchSession).toHaveBeenCalledWith(
        "",
        "catalyst-query",
        undefined,
        "openmrs-hiv",
        undefined,
        "CD4 cohort review",
      ),
    );

    await user.type(
      await screen.findByLabelText("Question"),
      "How many CD4 results?",
    );
    await user.click(screen.getByRole("button", { name: "Generate query" }));

    // The question seeds that session rather than opening a second one.
    await waitFor(() =>
      expect(client.askWorkbenchSessionQuestion).toHaveBeenCalledWith(
        "empty-session",
        "How many CD4 results?",
        "catalyst-query",
      ),
    );
  });

  it("renders a recorded run once, in the cell that owns its query version", async () => {
    const client = api();
    const failed: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: version.queryDigest,
      idempotencyKey: "idem-1",
      validationStatus: "valid",
      query: { sql: version.sql, parameters: [] },
      statementTimeoutMs: 30000,
      maxRows: 1000,
      replayed: false,
      status: "failed",
      databaseDiagnostic: {
        sqlstate: "42703",
        severity: "ERROR",
        message: 'column "test_type" does not exist',
        detail: null,
        hint: null,
        position: 214,
      },
      durationMs: 18,
      executionId: "88888888-8888-4888-8888-888888888888",
      sessionId: session.sessionId,
      versionId: version.versionId,
      ordinal: 1,
      completedAt: "2026-08-06T00:00:05Z",
    };
    client.getWorkbenchSession = vi
      .fn()
      .mockResolvedValue({ ...session, executions: [failed] });
    window.localStorage.setItem(
      "catalyst.workbench.activeSessionId",
      session.sessionId,
    );
    render(<QueryWorkspace api={client} />);

    // The notebook cell owns the run. The workbench panel below must not
    // repeat it, or the same failure is reported twice on one page.
    const diagnostic = await screen.findAllByText(
      'column "test_type" does not exist',
    );
    expect(diagnostic).toHaveLength(1);
    expect(diagnostic[0]!.closest(".query-turn")).not.toBeNull();
  });
});
