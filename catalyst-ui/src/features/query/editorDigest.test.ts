import { describe, expect, it } from "vitest";
import {
  editorContentMatchesVersion,
  normalizeSqlLayout,
  workbenchEditorDigest,
} from "./editorDigest";

describe("workbench editor digest", () => {
  it("matches the gateway RFC 8785 golden vector", () => {
    expect(
      workbenchEditorDigest({
        sql: "SELECT 1",
        parameters: [],
        expectedColumns: [],
      }),
    ).toBe(
      "82d9696f92e64acb0c4edba843633c97eb23fd3f22887d93755eb86971855105",
    );
  });
});

describe("normalizeSqlLayout", () => {
  it("treats a reformat as the same query", () => {
    const dense =
      "SELECT medication_name, COUNT(*) AS request_count FROM t WHERE g = :gender GROUP BY medication_name";
    const pretty = [
      "SELECT",
      "  medication_name,",
      "  COUNT(*) AS request_count",
      "FROM t",
      "WHERE",
      "  g = :gender",
      "GROUP BY",
      "  medication_name",
    ].join("\n");

    expect(normalizeSqlLayout(pretty)).toBe(normalizeSqlLayout(dense));
  });

  it("ignores keyword case", () => {
    expect(normalizeSqlLayout("select a from t")).toBe(
      normalizeSqlLayout("SELECT a FROM t"),
    );
  });

  it("leaves string literals alone, spaces and case included", () => {
    // 'HIV viral load' and '990D9%' are data. Collapsing their spaces or
    // lowercasing them would quietly change what the query asks for.
    const sql = "SELECT TO_CHAR(v, '990D9%') FROM t WHERE n = 'HIV viral load'";
    const normalized = normalizeSqlLayout(sql);
    expect(normalized).toContain("'990D9%'");
    expect(normalized).toContain("'HIV viral load'");
  });

  it("does not conflate queries that differ in a literal", () => {
    expect(normalizeSqlLayout("SELECT 'a b'")).not.toBe(
      normalizeSqlLayout("SELECT 'ab'"),
    );
  });

  it("keeps a dollar-quoted body verbatim", () => {
    const normalized = normalizeSqlLayout("SELECT $$100%  x$$ AS b");
    expect(normalized).toContain("$$100%  x$$");
  });

  it("still sees a real edit as a change", () => {
    expect(normalizeSqlLayout("SELECT a FROM t")).not.toBe(
      normalizeSqlLayout("SELECT b FROM t"),
    );
  });

  it("survives an escaped quote inside a literal", () => {
    const normalized = normalizeSqlLayout("SELECT 'it''s  here' FROM t");
    expect(normalized).toContain("'it''s  here'");
  });
});

describe("editorContentMatchesVersion", () => {
  const version = {
    sql: "SELECT a,   b FROM t",
    parameters: [],
    expectedColumns: [],
  } as unknown as Parameters<typeof editorContentMatchesVersion>[1];

  it("matches when only the layout differs", () => {
    expect(
      editorContentMatchesVersion(
        { sql: "SELECT\n  a,\n  b\nFROM t", parameters: [], expectedColumns: [] },
        version,
      ),
    ).toBe(true);
  });

  it("does not match a genuine edit", () => {
    expect(
      editorContentMatchesVersion(
        { sql: "SELECT a FROM t", parameters: [], expectedColumns: [] },
        version,
      ),
    ).toBe(false);
  });
});
