import { describe, expect, it } from "vitest";
import { highlightSql, sqlHighlightStyle } from "./sqlHighlight";

const SQL = `SELECT date_trunc('month', result_date) AS month,
       count(*) AS n
FROM analytics.lab_result_fact_v1
WHERE test_name = 'HIV viral load' -- only viral load
GROUP BY 1
ORDER BY 1`;

describe("highlightSql", () => {
  it("reproduces the source exactly, character for character", () => {
    // The reader's SQL is the one thing highlighting may never cost them.
    expect(highlightSql(SQL).map((span) => span.text).join("")).toBe(SQL);
  });

  it("styles the parts a reader scans for", () => {
    const spans = highlightSql(SQL);
    const classFor = (text: string) =>
      spans.find((span) => span.text === text)?.className;

    expect(classFor("SELECT")).toContain("sql-keyword");
    expect(classFor("FROM")).toContain("sql-keyword");
    expect(classFor("'HIV viral load'")).toContain("sql-string");
    expect(spans.some((span) => span.className?.includes("sql-comment"))).toBe(
      true,
    );
  });

  it("returns the source unstyled rather than nothing when it cannot parse", () => {
    const broken = "SELECT ((( FROM";
    expect(highlightSql(broken).map((span) => span.text).join("")).toBe(broken);
  });

  it("is empty for empty input", () => {
    expect(highlightSql("")).toEqual([]);
  });

  it("exports one style, so the editor and a cell cannot disagree", () => {
    // Both renderers import this object. If it were duplicated, a colour
    // could be changed in one place and not the other.
    expect(sqlHighlightStyle).toBeDefined();
  });
});
