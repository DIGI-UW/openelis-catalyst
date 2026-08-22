import { useEffect, useState } from "react";
import type { CatalystApi } from "../api";
import type { BoundParameter, WorkbenchEditorCatalog } from "../types";

/**
 * The editor buffer: the SQL and parameters being drafted, the catalog that
 * grounds completion, and the editor's own chrome (wrap, focus, open).
 *
 * One of five hooks extracted from QueryWorkspace. The buffer is what the
 * person is writing; whether it counts as an edit is judged elsewhere
 * (editorDigest), and what running it means is a workflow the component
 * composes — this hook owns where the draft lives.
 */
export const useEditorBuffer = (api: CatalystApi, dataSourceId: string) => {
  const [sql, setSql] = useState("");
  const [parameters, setParameters] = useState<BoundParameter[]>([]);
  const [catalog, setCatalog] = useState<WorkbenchEditorCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);
  const [focusRequestId, setFocusRequestId] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);

  useEffect(() => {
    if (!api.getWorkbenchCatalog) return;
    const controller = new AbortController();
    api.getWorkbenchCatalog(dataSourceId || undefined, controller.signal)
      .then((loaded) => {
        setCatalog(loaded);
        setCatalogFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setCatalog(null);
          setCatalogFailed(true);
        }
      });
    return () => controller.abort();
  }, [api, dataSourceId]);

  return {
    sql,
    setSql,
    parameters,
    setParameters,
    catalog,
    catalogFailed,
    wrapLines,
    setWrapLines,
    focusRequestId,
    setFocusRequestId,
    editorOpen,
    setEditorOpen,
  };
};
