import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CatalystApi } from "../api";
import type {
  DatasetOverview,
  DatasetRows,
  WorkbenchEditorCatalog,
} from "../types";
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

const catalog: WorkbenchEditorCatalog = {
  contractVersion: "catalyst.workbench.editor-catalog.v1",
  catalogVersion: "analytics-catalog-v1",
  schemaVersion: "analytics-v1",
  dialect: "postgresql",
  schemas: [
    {
      name: "fhir",
      views: [
        {
          name: "patient_flat_v1",
          qualifiedName: "fhir.patient_flat_v1",
          grain: "Exactly one row per FHIR Patient.",
          columns: [
            {
              name: "patient_id",
              logicalType: "string",
              nullable: false,
              description: "FHIR Patient resource identifier.",
            },
          ],
        },
      ],
    },
    {
      name: "analytics",
      views: [
        {
          name: "lab_result_fact_v1",
          qualifiedName: "analytics.lab_result_fact_v1",
          grain: "Exactly one row per FHIR Observation.",
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
              unitColumn: "result_unit",
              description: "Numeric FHIR Quantity value.",
            },
          ],
        },
      ],
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
        name: "Preview available laboratory records",
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
        name: "Preview available laboratory records",
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

  it("shows every catalog relation compactly and reveals its columns on demand", async () => {
    const api = makeApi(vi.fn().mockResolvedValue(oneRow));
    const user = userEvent.setup();

    render(<DatasetBrowser api={api} catalog={catalog} />);

    expect(
      await screen.findByRole("heading", { name: "Supported query schema" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        "2 relations available. Expand a relation to see its columns.",
      ),
    ).toBeVisible();

    const relationButtons = screen.getAllByRole("button", {
      name: /(?:analytics\.lab_result_fact_v1|fhir\.patient_flat_v1)/,
    });
    expect(relationButtons).toHaveLength(2);
    const labRelationButton = relationButtons[0]!;
    const patientRelationButton = relationButtons[1]!;
    expect(labRelationButton).toHaveAccessibleName(
      "analytics.lab_result_fact_v1 2 columns",
    );
    expect(patientRelationButton).toHaveAccessibleName(
      "fhir.patient_flat_v1 1 column",
    );
    expect(labRelationButton).toHaveAttribute("aria-expanded", "false");

    await user.click(labRelationButton);

    expect(labRelationButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Exactly one row per FHIR Observation/)).toBeVisible();
    expect(screen.getByText("result_value")).toBeVisible();
    expect(screen.getByText("Unit from")).toBeVisible();
    expect(screen.getByText("result_unit")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Preview available laboratory records" }),
    ).toHaveAttribute("aria-expanded", "false");
  });
});
