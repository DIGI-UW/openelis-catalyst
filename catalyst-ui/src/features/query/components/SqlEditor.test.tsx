import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SqlEditor } from "./SqlEditor";
import {
  buildSqlCompletionSchema,
  formatPostgresqlSql,
  type SqlCatalogRelation,
} from "./sqlEditorSupport";

const SQL =
  "select patient_id, result_value from analytics.lab_result_fact_v1 where result_value > :minimum_value";

const catalog: SqlCatalogRelation[] = [
  {
    schema: "public",
    name: "facilities",
    columns: ["name", "id"],
  },
  {
    schema: "analytics",
    name: "lab_result_fact_v1",
    columns: ["result_value", "patient_id", "result_value"],
  },
  {
    schema: "analytics",
    name: "lab_result_fact_v1",
    columns: ["observed_at"],
  },
];

describe("SqlEditor", () => {
  it("renders a labelled PostgreSQL editor with syntax tokens and line numbers", () => {
    const { container } = render(
      <SqlEditor label="Generated SQL" value={SQL} onChange={vi.fn()} />,
    );

    const editor = screen.getByRole("textbox", { name: "Generated SQL" });
    expect(editor).toHaveAttribute("aria-multiline", "true");
    expect(container.querySelector("[data-language='postgresql']")).toBeVisible();
    expect(container.querySelector(".cm-lineNumbers")).toBeVisible();
    expect(
      container.querySelector(
        ".cm-lineNumbers .cm-gutterElement:not([style*='visibility: hidden'])",
      ),
    ).toHaveTextContent("1");
    expect(container.querySelector(".cm-content .cm-line span[class]")).toBeVisible();
  });

  it("wraps by default and exposes a retained-state-friendly toggle", async () => {
    const user = userEvent.setup();
    const onWrapLinesChange = vi.fn();
    const { container } = render(
      <SqlEditor
        label="Generated SQL"
        value={SQL}
        onChange={vi.fn()}
        onWrapLinesChange={onWrapLinesChange}
      />,
    );

    const toggle = screen.getByRole("button", { name: "Wrap lines" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".cm-lineWrapping")).toBeVisible();

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(container.querySelector(".cm-lineWrapping")).not.toBeInTheDocument();
    expect(onWrapLinesChange).toHaveBeenLastCalledWith(false);
  });

  it("formats PostgreSQL deterministically and preserves named placeholders", async () => {
    const once = formatPostgresqlSql(SQL);
    const twice = formatPostgresqlSql(once);

    expect(once).toBe(twice);
    expect(once).toContain(":minimum_value");
    expect(once).toContain("SELECT");

    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SqlEditor label="Generated SQL" value={SQL} onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Format SQL" }));
    expect(onChange).toHaveBeenLastCalledWith(once);
  });

  it("converts catalog relations into stable schema namespaces for completion", () => {
    expect(buildSqlCompletionSchema(catalog)).toEqual({
      analytics: {
        lab_result_fact_v1: ["observed_at", "patient_id", "result_value"],
      },
      public: {
        facilities: ["id", "name"],
      },
    });
    expect(buildSqlCompletionSchema([...catalog].reverse())).toEqual(
      buildSqlCompletionSchema(catalog),
    );
    expect(buildSqlCompletionSchema([])).toEqual({});
  });

  it("emits direct edits through onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SqlEditor label="Generated SQL" value="" onChange={onChange} />);

    const editor = screen.getByRole("textbox", { name: "Generated SQL" });
    await user.click(editor);
    await user.keyboard("SELECT 1");

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("SELECT 1"));
  });

  it("leaves Tab available for keyboard focus navigation", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SqlEditor label="Generated SQL" value="SELECT 1" onChange={onChange} />);

    const editor = screen.getByRole("textbox", { name: "Generated SQL" });
    await user.click(editor);
    await user.tab();

    expect(screen.getByRole("button", { name: "Wrap lines" })).toHaveFocus();
    expect(onChange).not.toHaveBeenCalledWith(expect.stringContaining("\t"));
  });

  it("waits to honor a focus request until a generated editor unlocks", async () => {
    const { rerender } = render(
      <SqlEditor
        label="Generated SQL"
        value="SELECT 1"
        onChange={vi.fn()}
        readOnly
        focusRequestId={1}
      />,
    );

    expect(screen.getByRole("textbox", { name: "Generated SQL" })).not.toHaveFocus();

    rerender(
      <SqlEditor
        label="Generated SQL"
        value="SELECT 2"
        onChange={vi.fn()}
        readOnly={false}
        focusRequestId={1}
      />,
    );

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Generated SQL" })).toHaveFocus(),
    );
  });
});
