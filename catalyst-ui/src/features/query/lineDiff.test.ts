import { describe, expect, it } from "vitest";
import { lineDiffSummary } from "./lineDiff";

describe("lineDiffSummary", () => {
  it("counts a pure addition", () => {
    expect(lineDiffSummary("SELECT a\nFROM t", "SELECT a\nFROM t\nLIMIT 5")).toEqual(
      { added: 1, removed: 0 },
    );
  });

  it("counts a pure removal", () => {
    expect(lineDiffSummary("SELECT a\nFROM t\nLIMIT 5", "SELECT a\nFROM t")).toEqual(
      { added: 0, removed: 1 },
    );
  });

  it("counts a changed line as one out, one in", () => {
    expect(lineDiffSummary("SELECT a\nFROM t", "SELECT b\nFROM t")).toEqual({
      added: 1,
      removed: 1,
    });
  });

  it("does not double-count a moved line", () => {
    // Set-difference counting would call this two changes.
    expect(
      lineDiffSummary("WHERE x\nORDER BY y", "ORDER BY y\nWHERE x"),
    ).toEqual({ added: 1, removed: 1 });
  });

  it("sees no change in identical text", () => {
    expect(lineDiffSummary("SELECT 1", "SELECT 1")).toEqual({
      added: 0,
      removed: 0,
    });
  });
});
