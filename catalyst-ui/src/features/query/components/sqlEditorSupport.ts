import type { SQLNamespace } from "@codemirror/lang-sql";
import { format } from "sql-formatter";

export interface SqlCatalogRelation {
  schema: string;
  name: string;
  columns: readonly string[];
}

const stableCompare = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

export const buildSqlCompletionSchema = (
  relations: readonly SqlCatalogRelation[],
): SQLNamespace => {
  const schemas = new Map<string, Map<string, Set<string>>>();

  for (const relation of relations) {
    const schema = relation.schema.trim();
    const name = relation.name.trim();
    if (!schema || !name) continue;

    const tables = schemas.get(schema) ?? new Map<string, Set<string>>();
    schemas.set(schema, tables);
    const columns = tables.get(name) ?? new Set<string>();
    tables.set(name, columns);
    for (const column of relation.columns) {
      const normalizedColumn = column.trim();
      if (normalizedColumn) columns.add(normalizedColumn);
    }
  }

  const namespace: Record<string, Record<string, readonly string[]>> = {};
  for (const schema of [...schemas.keys()].sort(stableCompare)) {
    const tables = schemas.get(schema);
    if (!tables) continue;
    namespace[schema] = {};
    for (const table of [...tables.keys()].sort(stableCompare)) {
      namespace[schema][table] = [...(tables.get(table) ?? [])].sort(stableCompare);
    }
  }
  return namespace;
};

// The editor formats in the source's declared dialect. sql-formatter names
// Spark's grammar "spark"; anything the gateway declares that it does not
// know falls back to standard SQL rather than to a specific engine.
const FORMATTER_LANGUAGES = new Set(["spark"]);

export const formatSql = (source: string, dialect: string) =>
  format(source, {
    language: (FORMATTER_LANGUAGES.has(dialect) ? dialect : "sql") as never,
    keywordCase: "upper",
    tabWidth: 2,
    useTabs: false,
    linesBetweenQueries: 1,
    // Without this, `:name` is not a token to the formatter and it emits
    // `test_name =:test_name` -- the parameter still binds, but the query reads
    // badly and the missing space shows up in anything that stores the
    // formatted text.
    paramTypes: { named: [":"] },
  });
