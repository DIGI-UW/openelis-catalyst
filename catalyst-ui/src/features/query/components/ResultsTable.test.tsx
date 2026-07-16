import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { allTaggedCells, table } from "../test/fixtures";
import type { CatalystTable, Column } from "../types";
import { ResultsTable } from "./ResultsTable";

const taggedColumns: Column[] = [
  { name: "text", logicalType: "string", nullable: false },
  { name: "count", logicalType: "integer", nullable: false },
  { name: "ratio", logicalType: "decimal", nullable: false },
  { name: "active", logicalType: "boolean", nullable: false },
  { name: "day", logicalType: "date", nullable: false },
  { name: "observed", logicalType: "date-time", nullable: false },
  { name: "optional", logicalType: "string", nullable: true },
];

describe("ResultsTable", () => {
  it("renders every catalyst.table.v1 tagged cell without losing precision", () => {
    const result: CatalystTable = {
      ...table,
      table: {
        columns: taggedColumns,
        rows: [allTaggedCells],
        rowCount: {
          returned: 1,
          total: 1,
          totalIsExact: true,
          truncated: false,
          limit: 100,
        },
      },
    };

    render(<ResultsTable result={result} />);

    const region = screen.getByRole("region", { name: "Query results" });
    expect(within(region).getByText("positive")).toBeVisible();
    expect(within(region).getByText("1200")).toBeVisible();
    expect(within(region).getByText("0.1250")).toBeVisible();
    expect(within(region).getByText("Yes")).toBeVisible();
    expect(within(region).getByText("2026-07-16")).toBeVisible();
    expect(within(region).getByText("2026-07-16T00:00:00Z")).toBeVisible();
    expect(within(region).getByText("—")).toHaveAccessibleName("No value");
  });

  it("renders a successful empty result distinctly", () => {
    const result: CatalystTable = {
      ...table,
      table: {
        ...table.table,
        rows: [],
        rowCount: {
          returned: 0,
          total: 0,
          totalIsExact: true,
          truncated: false,
          limit: 100,
        },
      },
    };

    render(<ResultsTable result={result} />);

    expect(screen.getByText("No rows matched this query.")).toBeVisible();
    expect(screen.getByText("0 rows returned")).toBeVisible();
  });

  it("makes row truncation and an inexact total explicit", () => {
    const result: CatalystTable = {
      ...table,
      table: {
        ...table.table,
        rowCount: {
          returned: 3,
          total: null,
          totalIsExact: false,
          truncated: true,
          limit: 3,
        },
      },
      warnings: ["The result reached the configured row limit."],
    };

    render(<ResultsTable result={result} />);

    expect(screen.getByText("Results truncated at 3 rows.")).toBeVisible();
    expect(screen.getByText("3 rows returned; total unknown")).toBeVisible();
    expect(
      screen.getByText("The result reached the configured row limit."),
    ).toBeVisible();
  });
});
