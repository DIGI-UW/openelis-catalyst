import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CatalystApi } from "../api";
import type { DatasetOverview, DatasetRows } from "../types";
import { DatasetBrowser } from "./DatasetBrowser";

const overview: DatasetOverview = {
  contractVersion: "catalyst.dataset-overview.v1",
  datasetId: "openelis-live-load",
  synthetic: true,
  patients: 1,
  results: 1,
  testTypes: 1,
  firstObservedAt: "2026-01-01T00:00:00Z",
  lastObservedAt: "2026-01-01T00:00:00Z",
  tests: [
    {
      testName: "Viral Load",
      unit: "copies/ml",
      results: 1,
      patients: 1,
      minimum: "9000",
      median: "9000",
      maximum: "9000",
    },
  ],
  exampleQuestions: [],
};

const oneRow: DatasetRows = {
  contractVersion: "catalyst.dataset-rows.v1",
  total: 1,
  limit: 25,
  offset: 0,
  rows: [
    {
      observationId: "observation-1",
      patientId: "patient-1",
      testName: "Viral Load",
      value: "9000",
      unit: "copies/ml",
      observedAt: "2026-01-01T00:00:00Z",
      issuedAt: "2026-01-01T01:00:00Z",
      turnaroundMinutes: "60",
    },
  ],
};

const makeApi = (getDatasetRows: CatalystApi["getDatasetRows"]): CatalystApi => ({
  submitQuestion: vi.fn(),
  executePreview: vi.fn(),
  pollExecution: vi.fn(),
  getDatasetOverview: vi.fn().mockResolvedValue(overview),
  getDatasetRows,
});

describe("DatasetBrowser", () => {
  it("renders a truthful empty state for filters with no matches", async () => {
    const api = makeApi(
      vi.fn().mockResolvedValue({ ...oneRow, total: 0, rows: [] }),
    );
    const user = userEvent.setup();

    render(<DatasetBrowser api={api} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Browse available laboratory records",
      }),
    );

    expect(
      screen.getByText("No laboratory records match these filters."),
    ).toBeVisible();
    expect(screen.queryByText(/showing 1–0/i)).not.toBeInTheDocument();
  });

  it("does not show stale rows after a filter request fails", async () => {
    const getDatasetRows = vi
      .fn()
      .mockResolvedValueOnce(oneRow)
      .mockRejectedValueOnce(new Error("Dataset rows are unavailable."));
    const api = makeApi(getDatasetRows);
    const user = userEvent.setup();

    render(<DatasetBrowser api={api} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Browse available laboratory records",
      }),
    );
    expect(await screen.findByText("9000 copies/ml")).toBeVisible();

    await user.type(screen.getByLabelText("Patient FHIR ID"), "missing-patient");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByText("Dataset rows are unavailable.")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByText("9000 copies/ml")).not.toBeInTheDocument(),
    );
  });
});
