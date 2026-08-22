import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  editorContentMatchesVersion,
  normalizeSqlLayout,
  parametersMatch,
  sqlLayoutMatches,
  workbenchEditorDigest,
} from "./editorDigest";

const featureSources = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return featureSources(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)
      ? [path]
      : [];
  });

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

describe("one owner for 'is this the same query?'", () => {
  // This rule was written out by hand at seven call sites across four files.
  // Each one compared SQL as bytes, so each one read a reformat as a rewrite --
  // mislabelling the model's query "Edited by hand", clearing its declared
  // columns, reporting a fresh result stale, and disabling "Save Dataset". They
  // were found one at a time over three rounds because nothing stopped the
  // eighth from being written the same way. This does.
  const sources = featureSources(join(__dirname));

  it("finds no hand-written SQL comparison outside this module", () => {
    const offenders = sources
      .filter((path) => !path.endsWith("editorDigest.ts"))
      .flatMap((path) =>
        readFileSync(path, "utf8")
          .split("\n")
          .map((line, index) => ({ path, line, number: index + 1 }))
          .filter(({ line }) => {
            // Comments are not comparisons.
            if (/^\s*(\/\/|\*)/.test(line)) return false;
            const code = line.replace(/\/\/.*$/, "");
            // An equality test whose operand IS sql -- `sql ===`, `.sql !==`,
            // `=== execution.query.sql`, `!== currentVersion?.sql`. The first
            // version of this guard required a literal `.sql` before the
            // operator, missed `?.sql`, and passed on a planted violation; a
            // guard is only kept after it has been watched failing.
            // `sql.trim().length === 0` does not match: the operand there is
            // `length`, not sql.
            return (
              /[\w$?.]*\bsql\b\s*(===|!==)/i.test(code) ||
              /(===|!==)\s*[\w$?.]*\bsql\b/i.test(code) ||
              /normalizeSqlLayout\s*\(/.test(code) ||
              // The parameters flavor of the same defect:
              // JSON.stringify(a) === JSON.stringify(b) over bound parameters.
              (/JSON\.stringify/.test(code) &&
                /(===|!==)/.test(code) &&
                /parameters/i.test(code))
            );
          }),
      )
      .map(({ path, line, number }) => `${path}:${number} ${line.trim()}`);

    // Call sqlLayoutMatches, or editorContentMatchesVersion for whole content.
    expect(offenders).toEqual([]);
  });

  it("scanned the files it claims to scan", () => {
    // A guard that silently matches nothing guards nothing.
    expect(sources.length).toBeGreaterThan(5);
    expect(sources).toContain(join(__dirname, "QueryWorkspace.tsx"));
    expect(sources).toContain(
      join(__dirname, "components", "WorkbenchPanel.tsx"),
    );
  });
});

describe("parametersMatch", () => {
  const parameter = {
    name: "gender",
    type: "string" as const,
    source: "question" as const,
    value: "female",
  };

  it("ignores how the objects happened to be built", () => {
    const reordered = {
      value: "female",
      source: "question" as const,
      type: "string" as const,
      name: "gender",
    };
    // JSON.stringify-equality read these as different parameters.
    expect(JSON.stringify([parameter])).not.toBe(JSON.stringify([reordered]));
    expect(parametersMatch([parameter], [reordered])).toBe(true);
  });

  it("sees a changed binding", () => {
    expect(
      parametersMatch([parameter], [{ ...parameter, value: "male" }]),
    ).toBe(false);
  });

  it("sees a different arity", () => {
    expect(parametersMatch([parameter], [])).toBe(false);
  });
});

describe("sqlLayoutMatches", () => {
  it("is true for a reflow and false for a real change", () => {
    expect(sqlLayoutMatches("SELECT a\n  FROM t", "select a from t")).toBe(true);
    expect(sqlLayoutMatches("SELECT a FROM t", "SELECT b FROM t")).toBe(false);
  });

  it("does not collapse spaces inside a literal", () => {
    expect(sqlLayoutMatches("SELECT 'a  b'", "SELECT 'a b'")).toBe(false);
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
