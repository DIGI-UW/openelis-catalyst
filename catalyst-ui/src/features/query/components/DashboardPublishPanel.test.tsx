import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CatalystApi } from "../api";
import type { DashboardBuilderEntity, WorkbenchSession } from "../types";
import { DashboardPublishPanel } from "./DashboardPublishPanel";

const queryVersion = {
  versionId: "query-v1",
  ordinal: 1,
  sql: "SELECT 1 AS value",
  parameters: [],
  expectedColumns: [{ name: "value", logicalType: "integer", nullable: false }],
};

const session = {
  sessionId: "session-1",
  currentVersionId: "query-v1",
  currentVersion: queryVersion,
  executions: [
    {
      executionId: "execution-1",
      versionId: "query-v1",
      status: "succeeded",
      ordinal: 1,
      durationMs: 4,
      query: { sql: queryVersion.sql, parameters: [] },
      result: {
        columns: [
          {
            ordinal: 1,
            name: "value",
            databaseType: "int4",
            typeOid: 23,
            logicalType: "integer",
          },
        ],
        rows: [[{ type: "integer", value: 1 }]],
        rowCount: { returned: 1, truncated: false, truncationReason: null },
      },
    },
  ],
  versions: [queryVersion],
} as unknown as WorkbenchSession;

const savedDataset: DashboardBuilderEntity = {
  id: "dataset-1",
  versionId: "dataset-v1",
  ordinal: 1,
  configuration: {
    title: "Count result",
    source: {
      sessionId: "session-1",
      executionId: "execution-1",
      dataSourceId: "openelis",
    },
    columns: [{ ordinal: 1, name: "value", logicalType: "integer" }],
    rowCount: { returned: 1 },
    parameters: [],
  },
  configurationDigest: "a".repeat(64),
  createdAt: "2026-08-06T00:00:00Z",
};

const savedWidget: DashboardBuilderEntity = {
  id: "widget-1",
  versionId: "widget-v1",
  ordinal: 1,
  configuration: {
    title: "Count KPI",
    datasetVersionId: "dataset-v1",
    presentationKind: "big_number",
  },
  configurationDigest: "b".repeat(64),
  createdAt: "2026-08-06T00:00:01Z",
};

const savedDashboard: DashboardBuilderEntity = {
  id: "dashboard-1",
  versionId: "dashboard-v1",
  ordinal: 1,
  configuration: {
    title: "Laboratory dashboard",
    widgets: [{ versionId: "widget-v1" }],
  },
  configurationDigest: "c".repeat(64),
  createdAt: "2026-08-06T00:00:02Z",
};

const olderWidget: DashboardBuilderEntity = {
  ...savedWidget,
  id: "widget-2",
  versionId: "widget-v2",
  configuration: {
    ...savedWidget.configuration,
    title: "Older table",
    presentationKind: "table",
  },
  configurationDigest: "e".repeat(64),
  createdAt: "2026-08-05T00:00:01Z",
};

const collection = (kind: "dataset" | "widget" | "dashboard", items: DashboardBuilderEntity[]) => ({
  contractVersion: "catalyst.dashboard-builder.v1" as const,
  kind,
  items,
});

const makeApi = (withSavedArtifacts = false) => ({
  listDashboardDatasets: vi.fn().mockResolvedValue(collection("dataset", withSavedArtifacts ? [savedDataset] : [])),
  listDashboardWidgets: vi.fn().mockResolvedValue(collection("widget", withSavedArtifacts ? [savedWidget] : [])),
  listDashboards: vi.fn().mockResolvedValue(collection("dashboard", withSavedArtifacts ? [savedDashboard] : [])),
  saveDashboardDataset: vi.fn().mockResolvedValue(savedDataset),
  saveDashboardWidget: vi.fn().mockResolvedValue(savedWidget),
  saveDashboard: vi.fn().mockResolvedValue(savedDashboard),
  publishDashboard: vi.fn().mockResolvedValue({
    status: "bundle_ready",
    dashboard: savedDashboard,
    pointer: { bundle: { fileName: "bundle.zip", sha256: "d".repeat(64), bytes: 1 } },
    downloadPath: "/bundle",
  }),
}) as unknown as CatalystApi;

describe("Dashboard Builder supervised promotion", () => {
  it("reviews and explicitly saves a Dataset before creating a Widget", async () => {
    const user = userEvent.setup();
    const api = makeApi();
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Review dataset draft" }));
    expect(screen.getByRole("heading", { name: "Results from Query v1" })).toBeVisible();
    await user.type(screen.getByLabelText("Dataset name"), "Count result");
    await user.click(screen.getByRole("button", { name: "Save Dataset" }));

    expect(api.saveDashboardDataset).toHaveBeenCalledWith({
      sessionId: "session-1",
      executionId: "execution-1",
      title: "Count result",
    });
    expect(await screen.findByRole("button", { name: "Review widget draft" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Review widget draft" }));
    await user.selectOptions(screen.getByLabelText("Visualization"), "big_number");
    await user.type(screen.getByLabelText("Widget name"), "Count KPI");
    await user.click(screen.getByRole("button", { name: "Save Widget" }));

    expect(api.saveDashboardWidget).toHaveBeenCalledWith({
      datasetVersionId: "dataset-v1",
      presentationKind: "big_number",
      title: "Count KPI",
    });
  });

  it("binds Dataset review to the selected successful execution and shows its complete evidence", async () => {
    const user = userEvent.setup();
    const evidenceSession = {
      ...session,
      profileId: "catalyst-query-split-models",
      dataSourceId: "openelis",
      catalogVersion: "catalog-2026.08",
      provenance: {
        catalystTraceId: "catalyst-trace-1",
        hubTraceId: "hub-trace-1",
        profileSnapshot: {
          profileLabel: "Gemma writer + Qwen reviewer",
          roleModels: {
            query_generate: "gemma-4-12b",
            query_review: "qwen2.5-14b",
          },
        },
      },
      currentVersion: {
        ...queryVersion,
        queryDigest: "sha256:query-v1",
        provenance: {
          catalystTraceId: "catalyst-trace-1",
          hubTraceId: "hub-trace-1",
        },
      },
      versions: [
        {
          ...queryVersion,
          queryDigest: "sha256:query-v1",
          provenance: {
            catalystTraceId: "catalyst-trace-1",
            hubTraceId: "hub-trace-1",
          },
        },
      ],
      validations: [
        {
          versionId: "query-v1",
          queryDigest: "sha256:query-v1",
          ordinal: 1,
          status: "warning",
          findings: [
            {
              findingId: "finding-1",
              ruleCode: "query.review",
              severity: "warning",
              message: "Confirm the intended reporting window.",
            },
          ],
        },
      ],
      executions: [
        {
          ...session.executions[0],
          queryDigest: "sha256:query-v1",
          query: {
            sql: "SELECT :minimum AS value",
            parameters: [
              { name: "minimum", type: "integer", source: "model", value: 1 },
            ],
          },
        },
        {
          executionId: "execution-2",
          versionId: "query-v1",
          queryDigest: "sha256:query-v1",
          status: "failed",
          ordinal: 2,
          durationMs: 2,
          query: { sql: queryVersion.sql, parameters: [] },
          databaseDiagnostic: { message: "Later database failure" },
        },
      ],
    } as unknown as WorkbenchSession;

    render(
      <DashboardPublishPanel
        api={makeApi()}
        session={evidenceSession}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Review dataset draft" }));
    const dialog = screen.getByRole("dialog", { name: "Review panel" });
    expect(within(dialog).getByRole("heading", { name: "Results from Query v1" })).toBeVisible();
    expect(within(dialog).queryByText("Later database failure")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("listitem")).toHaveTextContent(
      "Confirm the intended reporting window.",
    );
    expect(within(dialog).getByText("None — run succeeded")).toBeVisible();
    expect(within(dialog).getByText("Gemma writer + Qwen reviewer")).toBeVisible();
    expect(within(dialog).getByText("gemma-4-12b")).toBeVisible();
    expect(within(dialog).getByText("qwen2.5-14b")).toBeVisible();
    expect(within(dialog).getByText("catalyst-trace-1")).toBeVisible();
    expect(within(dialog).getByText("hub-trace-1")).toBeVisible();
    expect(within(dialog).getByText("catalog-2026.08")).toBeVisible();
    expect(within(dialog).getByText(":minimum")).toBeVisible();
    expect(within(dialog).getByText("integer")).toBeVisible();
    expect(within(dialog).getByText("1", { selector: "dd" })).toBeVisible();
  });

  it("opens a saved Dataset from the library in the same evidence panel", async () => {
    const user = userEvent.setup();
    render(
      <DashboardPublishPanel
        api={makeApi(true)}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="datasets"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Datasets" });
    await user.click(screen.getByRole("button", { name: "Review Count result" }));
    expect(screen.getByRole("heading", { name: "Review saved Dataset" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Results from Query v1" })).toBeVisible();
    // A saved Dataset's footer is the next step, not a spent button.
    expect(
      screen.getByRole("button", { name: "Build a widget from this Dataset" }),
    ).toBeEnabled();
  });

  it("hydrates an older saved Dataset from its recorded source session", async () => {
    const user = userEvent.setup();
    const olderDataset = {
      ...savedDataset,
      id: "dataset-older",
      versionId: "dataset-v-older",
      configuration: {
        ...savedDataset.configuration,
        title: "Older session Dataset",
        source: {
          sessionId: "session-older",
          executionId: "execution-older",
          dataSourceId: "openelis",
          catalogVersion: "catalog-older",
        },
      },
    };
    const newerVersion = {
      ...queryVersion,
      versionId: "query-v2",
      ordinal: 2,
      sql: "SELECT 2 AS value",
    };
    const olderSession = {
      ...session,
      sessionId: "session-older",
      currentVersionId: newerVersion.versionId,
      currentVersion: newerVersion,
      versions: [queryVersion, newerVersion],
      executions: [
        {
          ...session.executions[0],
          executionId: "execution-older",
          result: {
            ...session.executions[0]!.result,
            rows: [[{ type: "integer", value: 77 }]],
          },
        },
      ],
    } as unknown as WorkbenchSession;
    const api = makeApi();
    vi.mocked(api.listDashboardDatasets!).mockResolvedValue(
      collection("dataset", [olderDataset]),
    );
    api.getWorkbenchSession = vi.fn().mockResolvedValue(olderSession);

    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="datasets"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Review Older session Dataset" }),
    );
    expect(await screen.findByRole("heading", { name: "Results from Query v1" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "77" })).toBeVisible();
    expect(screen.queryByText("Stale — editor has changes")).not.toBeInTheDocument();
    expect(api.getWorkbenchSession).toHaveBeenCalledWith("session-older");
  });

  it("pages the complete bounded Dataset result without changing its execution", async () => {
    const user = userEvent.setup();
    const pagedSession = {
      ...session,
      executions: [
        {
          ...session.executions[0],
          result: {
            ...session.executions[0]!.result,
            rows: Array.from({ length: 26 }, (_, index) => [
              { type: "integer", value: index + 1 },
            ]),
            rowCount: { returned: 26, truncated: false, truncationReason: null },
          },
        },
      ],
    } as unknown as WorkbenchSession;
    render(
      <DashboardPublishPanel
        api={makeApi()}
        session={pagedSession}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Review dataset draft" }));
    expect(screen.getByText("Showing 1–25 of 26 returned rows")).toBeVisible();
    expect(screen.queryByRole("cell", { name: "26" })).not.toBeInTheDocument();
    const review = screen.getByRole("dialog");
    await user.click(
      within(review).getByRole("button", { name: "Next result page" }),
    );
    expect(screen.getByText("Showing 26–26 of 26 returned rows")).toBeVisible();
    expect(screen.getByRole("cell", { name: "26" })).toBeVisible();
    expect(within(review).getByRole("button", { name: "Previous result page" })).toBeEnabled();
  });

  it("does not duplicate a Dataset save while persistence is pending and retains failures", async () => {
    const user = userEvent.setup();
    let rejectSave: ((reason?: unknown) => void) | undefined;
    const api = makeApi();
    api.saveDashboardDataset = vi.fn().mockReturnValue(
      new Promise((_, reject) => {
        rejectSave = reject;
      }),
    );
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Review dataset draft" }));
    const save = screen.getByRole("button", { name: "Save Dataset" });
    await user.click(save);
    await user.click(save);
    expect(api.saveDashboardDataset).toHaveBeenCalledTimes(1);
    rejectSave?.(new Error("Dataset persistence is unavailable; retry without rerunning."));
    expect(await screen.findByText("Dataset persistence is unavailable; retry without rerunning.")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "Review panel" })).toBeVisible();
  });

  it("lists saved artifacts and publishes a selected Dashboard to the outbox", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Laboratory dashboard" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Publish to Superset" }));

    expect(api.publishDashboard).toHaveBeenCalledWith("dashboard-v1");
    expect(await screen.findByText("Superset bundle ready")).toBeVisible();
    expect(screen.getByRole("link", { name: "Download bundle" })).toHaveAttribute("href", "/bundle");
  });

  it("starts a new Dashboard with only the newest Widget selected", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    vi.mocked(api.listDashboardWidgets!).mockResolvedValue(
      collection("widget", [savedWidget, olderWidget]),
    );
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Laboratory dashboard" });
    await user.click(screen.getByRole("button", { name: "New Dashboard" }));

    expect(screen.getByRole("checkbox", { name: "Count KPI" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Older table" })).not.toBeChecked();
  });

  it("shows the deterministic chart suggestion, compatible overrides, and incompatibility reasons", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="widgets"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Widgets" });
    await user.click(screen.getByRole("button", { name: "New Widget" }));
    const dialog = screen.getByRole("dialog", { name: "Review panel" });
    expect(within(dialog).getByText("Suggested: Big number")).toBeVisible();
    expect(within(dialog).getByText(/Time-series line requires a temporal and numeric column/i)).toBeVisible();
    expect(within(dialog).getByRole("option", { name: "Table" })).toBeEnabled();
    expect(within(dialog).getByRole("option", { name: "Big number" })).toBeEnabled();
    expect(within(dialog).queryByRole("option", { name: "Time-series line" })).not.toBeInTheDocument();

    await user.selectOptions(within(dialog).getByLabelText("Visualization"), "table");
    expect(within(dialog).getByText("Columns: value")).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Save Widget" }));
    expect(api.saveDashboardWidget).toHaveBeenCalledWith({
      datasetVersionId: "dataset-v1",
      presentationKind: "table",
    });
  });

  it("preserves deterministic append order when composing a Dashboard", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    vi.mocked(api.listDashboardWidgets!).mockResolvedValue(
      collection("widget", [savedWidget, olderWidget]),
    );
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Dashboards" });
    await user.click(screen.getByRole("button", { name: "New Dashboard" }));
    await user.click(screen.getByRole("checkbox", { name: "Older table" }));
    await user.click(screen.getByRole("button", { name: "Save Dashboard" }));

    expect(api.saveDashboard).toHaveBeenCalledWith({
      widgetVersionIds: ["widget-v1", "widget-v2"],
    });
  });

  it("surfaces source and catalog mismatch rejection without closing the Dashboard review", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    api.saveDashboard = vi.fn().mockRejectedValue(
      new Error("A dashboard cannot mix data sources or catalog versions."),
    );
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Dashboards" });
    await user.click(screen.getByRole("button", { name: "New Dashboard" }));
    await user.click(screen.getByRole("button", { name: "Save Dashboard" }));

    expect(await screen.findByText("A dashboard cannot mix data sources or catalog versions.")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "Review panel" })).toBeVisible();
  });

  it("starts Dashboard composition from an immutable Widget library item", async () => {
    const user = userEvent.setup();
    const api = makeApi(true);
    vi.mocked(api.listDashboardWidgets!).mockResolvedValue(
      collection("widget", [savedWidget, olderWidget]),
    );
    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="widgets"
        onNavigate={vi.fn()}
      />,
    );

    await screen.findByRole("heading", { name: "Widgets" });
    await user.click(screen.getByRole("button", { name: "Add Older table to dashboard" }));
    expect(screen.getByRole("checkbox", { name: "Older table" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Count KPI" })).not.toBeChecked();
  });

  it("shows an exact verified import and opens its Superset dashboard", async () => {
    const api = makeApi(true);
    api.getDashboardPublication = vi.fn().mockResolvedValue({
      status: "imported",
      dashboard: savedDashboard,
      pointer: {
        bundle: { fileName: "bundle.zip", sha256: "d".repeat(64), bytes: 1 },
      },
      downloadPath: "/bundle",
      importState: {
        outcome: "imported",
        receiptId: "receipt-1",
        receiptDigest: "receipt-digest-1",
        dashboardUrl:
          "http://localhost:18088/superset/dashboard/catalyst-dashboard-1/",
      },
    });

    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByText("Imported")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Publish to Superset" }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Superset" })).toHaveAttribute(
      "href",
      "http://localhost:18088/superset/dashboard/catalyst-dashboard-1/",
    );
  });

  it("does not offer Open Superset after an import failure", async () => {
    const api = makeApi(true);
    api.getDashboardPublication = vi.fn().mockResolvedValue({
      status: "import_failed",
      dashboard: savedDashboard,
      pointer: {
        bundle: { fileName: "bundle.zip", sha256: "d".repeat(64), bytes: 1 },
      },
      downloadPath: "/bundle",
      importState: {
        outcome: "import_failed",
        errorCode: "superset_cli_import_failed",
        recoveryAction: "retry_same_bundle",
      },
    });

    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByText("Import failed")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Open Superset" }))
      .not.toBeInTheDocument();
    expect(screen.getByText(/Run the local Superset import helper again for this exact bundle/i)).toBeVisible();
    expect(screen.getByText(/superset_cli_import_failed/i)).toBeVisible();
  });

  it("requires exact import evidence before claiming Imported or offering Open Superset", async () => {
    const api = makeApi(true);
    api.getDashboardPublication = vi.fn().mockResolvedValue({
      status: "imported",
      dashboard: savedDashboard,
      pointer: {
        bundle: { fileName: "bundle.zip", sha256: "d".repeat(64), bytes: 1 },
      },
      downloadPath: "/bundle",
      importState: {
        outcome: "imported",
        receiptId: "receipt-without-digest",
      },
    });

    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByText("Import failed")).toBeVisible();
    expect(screen.queryByText("Imported")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Superset" })).not.toBeInTheDocument();
    expect(screen.getByText(/receipt does not exactly match this Dashboard version/i)).toBeVisible();
    expect(screen.getByRole("link", { name: "Download bundle" })).toHaveAttribute("href", "/bundle");
  });

  it("gives bounded full-reset guidance after post-import verification failure", async () => {
    const api = makeApi(true);
    api.getDashboardPublication = vi.fn().mockResolvedValue({
      status: "import_failed",
      dashboard: savedDashboard,
      pointer: {
        bundle: { fileName: "bundle.zip", sha256: "d".repeat(64), bytes: 1 },
      },
      downloadPath: "/bundle",
      importState: {
        outcome: "import_failed",
        receiptId: "receipt-2",
        receiptDigest: "receipt-digest-2",
        errorCode: "last_verified_mismatch",
        recoveryAction: "full_reset_then_reimport_last_verified_bundle",
      },
    });

    render(
      <DashboardPublishPanel
        api={api}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="dashboards"
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findByText("Import failed")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Open Superset" })).not.toBeInTheDocument();
    expect(screen.queryByText("Imported")).not.toBeInTheDocument();
    expect(screen.getByText(/fully reset only its metadata database and home volumes/i)).toBeVisible();
    expect(screen.getByText(/reimport and verify this Dashboard's last-verified bundle/i)).toBeVisible();
    expect(screen.getByText(/Do not delete individual assets/i)).toBeVisible();
    expect(screen.getByText(/last_verified_mismatch/i)).toBeVisible();
  });

  it("retains the previous execution as a stale Dataset after a successor is generated", async () => {
    const user = userEvent.setup();
    const successorSession = {
      ...session,
      currentVersionId: "query-v2",
      currentVersion: {
        ...queryVersion,
        versionId: "query-v2",
        ordinal: 2,
        sql: "SELECT 2 AS value",
      },
      versions: [
        ...session.versions,
        { ...queryVersion, versionId: "query-v2", ordinal: 2, sql: "SELECT 2 AS value" },
      ],
    } as unknown as WorkbenchSession;

    render(
      <DashboardPublishPanel
        api={makeApi()}
        session={successorSession}
        sql="SELECT 2 AS value"
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Review dataset draft" });
    expect(trigger).toBeVisible();
    expect(screen.getByText("Stale · rerun the visible query before saving")).toBeVisible();
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Review panel" });
    expect(within(dialog).getByText("Query v1", { exact: true })).toBeVisible();
    expect(within(dialog).getByText("Query v1 SQL snapshot", { exact: true })).toBeVisible();
    expect(within(dialog).queryByText("Query v2", { exact: true })).not.toBeInTheDocument();
  });

  it("contains keyboard focus in the review dialog and restores its trigger", async () => {
    const user = userEvent.setup();
    render(
      <DashboardPublishPanel
        api={makeApi()}
        session={session}
        sql={queryVersion.sql}
        parameters={[]}
        activeSection="ask"
        onNavigate={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Review dataset draft" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Review panel" });
    const closeButtons = within(dialog).getAllByRole("button", { name: "Close" });
    expect(closeButtons[0]).toHaveFocus();

    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(closeButtons.at(-1)).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });
});
