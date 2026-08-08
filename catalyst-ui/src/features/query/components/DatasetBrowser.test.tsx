import { render, screen, waitFor, within } from "@testing-library/react";
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
      await screen.findByText("Preview available laboratory records"),
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
      await screen.findByText("Preview available laboratory records"),
    );
    expect(await screen.findByText("9000 copies/ml")).toBeVisible();

    await user.type(screen.getByLabelText("Patient FHIR ID"), "missing-patient");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByText("Dataset rows are unavailable.")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByText("9000 copies/ml")).not.toBeInTheDocument(),
    );
  });

  it("browses one relation at a time and keeps full column detail available", async () => {
    const api = makeApi(vi.fn().mockResolvedValue(oneRow));
    const user = userEvent.setup();

    render(<DatasetBrowser api={api} catalog={catalog} />);

    // Every relation stays reachable — the rail picks between them rather
    // than dropping the ones it cannot fit.
    const relations = await screen.findByRole("combobox", { name: "Relation" });
    expect(
      within(relations).getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["analytics.lab_result_fact_v1", "fhir.patient_flat_v1"]);

    // The first relation is shown without an extra click.
    expect(screen.getByText("2 columns · postgresql")).toBeVisible();
    expect(screen.getByText("result_value")).toBeVisible();
    expect(screen.getByText(/Exactly one row per FHIR Observation/)).toBeVisible();

    // Nothing the page version showed is lost. Nullability, the unit
    // relationship and the description are rendered but width-gated: the
    // container query brings them back as the rail is dragged out, so at rail
    // width they are in the document rather than visible.
    expect(screen.getByText("Unit from")).toBeInTheDocument();
    expect(screen.getByText("result_unit")).toBeInTheDocument();
    expect(
      screen.getByText("Numeric FHIR Quantity value."),
    ).toBeInTheDocument();

    await user.selectOptions(relations, "fhir.patient_flat_v1");
    expect(screen.getByText("1 column · postgresql")).toBeVisible();
    expect(screen.queryByText("result_value")).not.toBeInTheDocument();
  });

  it("filters columns within the selected relation and reports the reduced count", async () => {
    const api = makeApi(vi.fn().mockResolvedValue(oneRow));
    const user = userEvent.setup();

    render(<DatasetBrowser api={api} catalog={catalog} />);

    await user.type(
      await screen.findByLabelText("Filter columns"),
      "result_value",
    );
    expect(screen.getByText("1 of 2 columns · postgresql")).toBeVisible();
    expect(screen.getByText("result_value")).toBeVisible();
  });

  it("inserts a column into the editor when the workspace offers it", async () => {
    const api = makeApi(vi.fn().mockResolvedValue(oneRow));
    const onInsertColumn = vi.fn();
    const user = userEvent.setup();

    render(
      <DatasetBrowser
        api={api}
        catalog={catalog}
        onInsertColumn={onInsertColumn}
      />,
    );

    await user.click(
      await screen.findByRole("button", {
        name: "Insert result_value into the SQL editor",
      }),
    );
    expect(onInsertColumn).toHaveBeenCalledWith("result_value");
  });
});
