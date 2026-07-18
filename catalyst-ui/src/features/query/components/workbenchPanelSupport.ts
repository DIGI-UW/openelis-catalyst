import type { WorkbenchEditorCatalog } from "../types";
import type { SqlCatalogRelation } from "./sqlEditorSupport";

const stableCompare = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

export const workbenchCatalogRelations = (
  catalog?: WorkbenchEditorCatalog | null,
): SqlCatalogRelation[] => {
  const relations = new Map<string, Set<string>>();
  const identities = new Map<string, { schema: string; name: string }>();

  for (const schema of catalog?.schemas ?? []) {
    const schemaName = schema.name.trim();
    if (!schemaName) continue;
    for (const view of schema.views) {
      const viewName = view.name.trim();
      if (!viewName) continue;
      const key = `${schemaName}\u0000${viewName}`;
      const columns = relations.get(key) ?? new Set<string>();
      relations.set(key, columns);
      identities.set(key, { schema: schemaName, name: viewName });
      for (const column of view.columns) {
        const name = column.name.trim();
        if (name) columns.add(name);
      }
    }
  }

  return [...relations.keys()]
    .sort(stableCompare)
    .map((key) => {
      const identity = identities.get(key)!;
      return {
        ...identity,
        columns: [...(relations.get(key) ?? [])].sort(stableCompare),
      };
    });
};
