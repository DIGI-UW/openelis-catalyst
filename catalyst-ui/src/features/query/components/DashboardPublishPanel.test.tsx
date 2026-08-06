import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CatalystApi } from "../api";
import type { WorkbenchSession } from "../types";
import { DashboardPublishPanel } from "./DashboardPublishPanel";

const session = {
  sessionId: "session-1",
  currentVersion: { versionId: "query-v1" },
  executions: [
    {
      executionId: "execution-1",
      versionId: "query-v1",
      status: "succeeded",
      ordinal: 1,
    },
  ],
} as unknown as WorkbenchSession;

const savedDataset = {
  id: "dataset-1",
  versionId: "dataset-v1",
  ordinal: 1,
  configuration: {},
  configurationDigest: "a".repeat(64),
  createdAt: "2026-08-06T00:00:00Z",
};

const savedWidget = {
  id: "widget-1",
  versionId: "widget-v1",
  ordinal: 1,
  configuration: {},
  configurationDigest: "b".repeat(64),
  createdAt: "2026-08-06T00:00:00Z",
};

describe("DashboardPublishPanel", () => {
  it("adds a chart widget from the saved query table", async () => {
    const user = userEvent.setup();
    const api = {
      saveDashboardDataset: vi.fn().mockResolvedValue(savedDataset),
      saveDashboardWidget: vi.fn().mockResolvedValue(savedWidget),
      saveDashboard: vi.fn().mockResolvedValue({
        ...savedWidget,
        id: "dashboard-1",
        versionId: "dashboard-v1",
      }),
      publishDashboard: vi.fn().mockResolvedValue({
        status: "bundle_ready",
        dashboard: savedWidget,
        pointer: { bundle: { fileName: "bundle.zip", sha256: "c".repeat(64), bytes: 1 } },
        downloadPath: "/bundle",
      }),
    } as unknown as CatalystApi;

    render(<DashboardPublishPanel api={api} session={session} />);

    await user.selectOptions(
      screen.getByLabelText("Visualization"),
      "time_series_line",
    );
    await user.click(screen.getByRole("button", { name: "Add widget" }));

    expect(api.saveDashboardDataset).toHaveBeenCalledWith({
      sessionId: "session-1",
      executionId: "execution-1",
    });
    expect(api.saveDashboardWidget).toHaveBeenCalledWith({
      datasetVersionId: "dataset-v1",
      presentationKind: "time_series_line",
    });
    expect(screen.getByText("1 widget ready")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Publish to Superset" }));
    expect(api.saveDashboard).toHaveBeenCalledWith({
      widgetVersionIds: ["widget-v1"],
    });
    expect(api.publishDashboard).toHaveBeenCalledWith("dashboard-v1");
  });

  it("restores the saved table and chart widgets for the current execution", async () => {
    const user = userEvent.setup();
    const restoredDataset = {
      ...savedDataset,
      configuration: {
        source: { sessionId: "session-1", executionId: "execution-1" },
      },
      createdAt: "2026-08-06T00:00:01Z",
    };
    const tableWidget = {
      ...savedWidget,
      versionId: "widget-table-v1",
      configuration: {
        title: "Laboratory result table",
        datasetVersionId: "dataset-v1",
        presentationKind: "table",
      },
      createdAt: "2026-08-06T00:00:02Z",
    };
    const chartWidget = {
      ...savedWidget,
      versionId: "widget-chart-v1",
      configuration: {
        title: "Laboratory result trend",
        datasetVersionId: "dataset-v1",
        presentationKind: "time_series_line",
      },
      createdAt: "2026-08-06T00:00:03Z",
    };
    const api = {
      listDashboardDatasets: vi.fn().mockResolvedValue({
        contractVersion: "catalyst.dashboard-builder.v1",
        kind: "dataset",
        items: [restoredDataset],
      }),
      listDashboardWidgets: vi.fn().mockResolvedValue({
        contractVersion: "catalyst.dashboard-builder.v1",
        kind: "widget",
        items: [chartWidget, tableWidget],
      }),
      saveDashboardDataset: vi.fn(),
      saveDashboardWidget: vi.fn(),
      saveDashboard: vi.fn().mockResolvedValue({
        ...savedWidget,
        id: "dashboard-1",
        versionId: "dashboard-v1",
      }),
      publishDashboard: vi.fn().mockResolvedValue({
        status: "bundle_ready",
        dashboard: savedWidget,
        pointer: { bundle: { fileName: "bundle.zip", sha256: "c".repeat(64), bytes: 1 } },
        downloadPath: "/bundle",
      }),
    } as unknown as CatalystApi;

    render(<DashboardPublishPanel api={api} session={session} />);

    expect(await screen.findByText("2 widgets ready")).toBeVisible();
    expect(screen.getByText("Laboratory result table")).toBeVisible();
    expect(screen.getByText("Laboratory result trend")).toBeVisible();
    expect(screen.getAllByText("Time-series line")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Publish to Superset" }));
    expect(api.saveDashboardDataset).not.toHaveBeenCalled();
    expect(api.saveDashboard).toHaveBeenCalledWith({
      widgetVersionIds: ["widget-table-v1", "widget-chart-v1"],
    });
  });
});
