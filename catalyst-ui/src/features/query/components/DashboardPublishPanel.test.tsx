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
    columns: [{ name: "value" }],
    rowCount: { returned: 1 },
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
        recoveryAction: "retry_import",
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
    expect(screen.getByText(/Run the local Superset import helper/i)).toBeVisible();
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
