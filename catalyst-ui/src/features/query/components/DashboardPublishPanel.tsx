import { CheckmarkFilled, Close, DataBase, Renew } from "@carbon/icons-react";
import { Button, InlineNotification, Select, SelectItem, Tag, TextInput } from "@carbon/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CatalystApi } from "../api";
import { editorContentMatchesVersion } from "../editorDigest";
import type {
  BoundParameter,
  DashboardBuilderEntity,
  DashboardBuilderSection,
  DashboardPresentationKind,
  DashboardPublication,
  WorkbenchSession,
} from "../types";
import { ExecutionResult } from "./WorkbenchPanel";
import "./DashboardPublishPanel.css";

interface DashboardPublishPanelProps {
  api: CatalystApi;
  session: WorkbenchSession | null;
  sql: string;
  parameters: BoundParameter[];
  activeSection: DashboardBuilderSection;
  disabled?: boolean;
  onNavigate: (section: DashboardBuilderSection) => void;
}

type ReviewPanel = "dataset" | "widget" | "dashboard" | null;

const presentations: Array<{ value: DashboardPresentationKind; label: string }> = [
  { value: "table", label: "Table" },
  { value: "big_number", label: "Big number" },
  { value: "time_series_line", label: "Time-series line" },
  { value: "time_series_area", label: "Time-series area" },
  { value: "grouped_bar", label: "Grouped bar" },
  { value: "stacked_bar", label: "Stacked bar" },
  { value: "proportion_bar", label: "100% stacked bar" },
];

const configurationValue = (entity: DashboardBuilderEntity, key: string) =>
  entity.configuration[key];

const configurationRecord = (entity: DashboardBuilderEntity, key: string) => {
  const value = configurationValue(entity, key);
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
};

const entityTitle = (entity: DashboardBuilderEntity, fallback: string) => {
  const title = configurationValue(entity, "title");
  return typeof title === "string" && title.trim() ? title : fallback;
};

const entityPresentation = (entity: DashboardBuilderEntity) => {
  const kind = configurationValue(entity, "presentationKind");
  return presentations.find((presentation) => presentation.value === kind)?.label ?? "Widget";
};

const dashboardWidgetVersionIds = (entity: DashboardBuilderEntity) => {
  const widgets = configurationValue(entity, "widgets");
  if (!Array.isArray(widgets)) return [];
  return widgets.flatMap((widget) => {
    if (typeof widget !== "object" || widget === null) return [];
    const versionId = (widget as Record<string, unknown>).versionId;
    return typeof versionId === "string" ? [versionId] : [];
  });
};

const newestSuccessfulExecution = (session: WorkbenchSession | null) =>
  session?.currentVersion
    ? session.executions
      .filter(
        (execution) =>
          execution.status === "succeeded" &&
          execution.versionId === session.currentVersion?.versionId,
      )
      .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null
    : null;

const presentationPreview = (kind: DashboardPresentationKind) => {
  if (kind === "table") return "Table preview using every returned column";
  if (kind === "big_number") return "Single-value KPI preview";
  if (kind.startsWith("time_series")) return "Time-series preview using the temporal and numeric columns";
  return "Bar chart preview using the categorical and numeric columns";
};

const dateLabel = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
};

export const DashboardPublishPanel = ({
  api,
  session,
  sql,
  parameters,
  activeSection,
  disabled = false,
  onNavigate,
}: DashboardPublishPanelProps) => {
  const [datasets, setDatasets] = useState<DashboardBuilderEntity[]>([]);
  const [widgets, setWidgets] = useState<DashboardBuilderEntity[]>([]);
  const [dashboards, setDashboards] = useState<DashboardBuilderEntity[]>([]);
  const [panel, setPanel] = useState<ReviewPanel>(null);
  const [datasetTitle, setDatasetTitle] = useState("");
  const [widgetTitle, setWidgetTitle] = useState("");
  const [dashboardTitle, setDashboardTitle] = useState("");
  const [presentationKind, setPresentationKind] =
    useState<DashboardPresentationKind>("table");
  const [selectedDatasetVersionId, setSelectedDatasetVersionId] = useState("");
  const [selectedWidgetVersionIds, setSelectedWidgetVersionIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(
    Boolean(api.listDashboardDatasets && api.listDashboardWidgets && api.listDashboards),
  );
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [publication, setPublication] = useState<DashboardPublication | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const execution = newestSuccessfulExecution(session);
  const editorMatchesCurrent = Boolean(
    session?.currentVersion &&
      editorContentMatchesVersion(
        {
          sql,
          parameters,
          expectedColumns:
            sql === session.currentVersion.sql
              ? session.currentVersion.expectedColumns
              : [],
        },
        session.currentVersion,
      ),
  );
  const resultIsStale = Boolean(execution && !editorMatchesCurrent);
  const supported = Boolean(
    api.saveDashboardDataset &&
      api.saveDashboardWidget &&
      api.saveDashboard &&
      api.publishDashboard,
  );

  const currentDataset = useMemo(
    () =>
      execution && session
        ? datasets.find((candidate) => {
            const source = configurationRecord(candidate, "source");
            return (
              source?.sessionId === session.sessionId &&
              source?.executionId === execution.executionId
            );
          }) ?? null
        : null,
    [datasets, execution, session],
  );

  useEffect(() => {
    if (!api.listDashboardDatasets || !api.listDashboardWidgets || !api.listDashboards) {
      return;
    }
    const controller = new AbortController();
    Promise.all([
      api.listDashboardDatasets(controller.signal),
      api.listDashboardWidgets(controller.signal),
      api.listDashboards(controller.signal),
    ])
      .then(([datasetCollection, widgetCollection, dashboardCollection]) => {
        if (controller.signal.aborted) return;
        setDatasets(datasetCollection.items);
        setWidgets(widgetCollection.items);
        setDashboards(dashboardCollection.items);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Catalyst could not load the dashboard libraries.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api]);

  const effectiveDatasetVersionId =
    selectedDatasetVersionId || currentDataset?.versionId || datasets[0]?.versionId || "";

  useEffect(() => {
    if (!panel) return;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPanel(null);
      window.setTimeout(() => returnFocusRef.current?.focus(), 0);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [panel]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const openPanel = (next: Exclude<ReviewPanel, null>, trigger?: HTMLElement) => {
    returnFocusRef.current = trigger ?? document.activeElement as HTMLElement | null;
    setError(null);
    setPublication(null);
    if (next === "dataset") {
      setDatasetTitle(currentDataset ? entityTitle(currentDataset, "Dataset") : "");
    }
    if (next === "widget") {
      setSelectedDatasetVersionId(currentDataset?.versionId ?? datasets[0]?.versionId ?? "");
    }
    if (next === "dashboard" && selectedWidgetVersionIds.length === 0) {
      setSelectedWidgetVersionIds(widgets.map((widget) => widget.versionId));
    }
    setPanel(next);
  };

  const closePanel = () => {
    setPanel(null);
    window.setTimeout(() => returnFocusRef.current?.focus(), 0);
  };

  const saveDataset = async () => {
    if (!session || !execution || !api.saveDashboardDataset || resultIsStale || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveDashboardDataset({
        sessionId: session.sessionId,
        executionId: execution.executionId,
        ...(datasetTitle.trim() ? { title: datasetTitle.trim() } : {}),
      });
      setDatasets((current) => [saved, ...current.filter((item) => item.versionId !== saved.versionId)]);
      setSelectedDatasetVersionId(saved.versionId);
      setToast(`“${entityTitle(saved, "Dataset")}” saved to Datasets.`);
      closePanel();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Catalyst could not save this Dataset.");
    } finally {
      setBusy(false);
    }
  };

  const saveWidget = async () => {
    if (!effectiveDatasetVersionId || !api.saveDashboardWidget || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveDashboardWidget({
        datasetVersionId: effectiveDatasetVersionId,
        ...(widgetTitle.trim() ? { title: widgetTitle.trim() } : {}),
        presentationKind,
      });
      setWidgets((current) => [saved, ...current.filter((item) => item.versionId !== saved.versionId)]);
      setSelectedWidgetVersionIds((current) =>
        current.includes(saved.versionId) ? current : [...current, saved.versionId],
      );
      setWidgetTitle("");
      setToast(`“${entityTitle(saved, "Widget")}” saved to Widgets.`);
      closePanel();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Catalyst could not save this Widget.");
    } finally {
      setBusy(false);
    }
  };

  const saveDashboard = async () => {
    if (!api.saveDashboard || selectedWidgetVersionIds.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveDashboard({
        widgetVersionIds: selectedWidgetVersionIds,
        ...(dashboardTitle.trim() ? { title: dashboardTitle.trim() } : {}),
      });
      setDashboards((current) => [saved, ...current.filter((item) => item.versionId !== saved.versionId)]);
      setToast(`“${entityTitle(saved, "Dashboard")}” saved to Dashboards.`);
      closePanel();
      onNavigate("dashboards");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Catalyst could not save this Dashboard.");
    } finally {
      setBusy(false);
    }
  };

  const publishDashboard = async (dashboard: DashboardBuilderEntity) => {
    if (!api.publishDashboard || busy) return;
    setBusy(true);
    setError(null);
    setPublication(null);
    try {
      const result = await api.publishDashboard(dashboard.versionId);
      setPublication(result);
      setToast(
        `“${entityTitle(dashboard, "Dashboard")}” is ready for the local Superset importer.`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Catalyst could not publish this Dashboard.",
      );
    } finally {
      setBusy(false);
    }
  };

  const renderAskArtifacts = () => {
    if (!session) return null;
    if (!supported) {
      return (
        <InlineNotification
          kind="info"
          lowContrast
          hideCloseButton
          title="Dashboard promotion is unavailable in this deployment."
        />
      );
    }
    if (!execution) {
      return (
        <p className="builder-empty-note">
          Run the current query to create a reviewable Dataset draft.
        </p>
      );
    }
    return (
      <section className="builder-artifacts" aria-label="Dashboard artifacts">
        <button
          type="button"
          className="builder-artifact-tile"
          disabled={disabled}
          onClick={(event) => openPanel("dataset", event.currentTarget)}
          aria-label="Review dataset draft"
        >
          <DataBase size={20} aria-hidden="true" />
          <span>
            <strong>{currentDataset ? entityTitle(currentDataset, "Saved Dataset") : `Dataset from Query v${session.currentVersion?.ordinal ?? "?"}`}</strong>
            <small>
              {resultIsStale
                ? "Stale · rerun the visible query before saving"
                : currentDataset
                  ? "Saved · review exact execution evidence"
                  : "Draft · review and save the current result"}
            </small>
          </span>
          <Tag type={resultIsStale ? "warm-gray" : currentDataset ? "green" : "blue"}>
            {resultIsStale ? "Stale" : currentDataset ? "Saved" : "Draft"}
          </Tag>
        </button>
        {currentDataset && (
          <button
            type="button"
            className="builder-artifact-tile"
            disabled={disabled}
            onClick={(event) => openPanel("widget", event.currentTarget)}
            aria-label="Review widget draft"
          >
            <span className="builder-artifact-tile__chart" aria-hidden="true">↗</span>
            <span>
              <strong>Create a Widget</strong>
              <small>Choose a compatible Superset visualization for this Dataset</small>
            </span>
            <Tag type="purple">Draft</Tag>
          </button>
        )}
      </section>
    );
  };

  const renderDatasets = () => (
    <section className="builder-library" aria-labelledby="datasets-title">
      <header className="builder-library__header">
        <div>
          <p className="eyebrow">Library</p>
          <h1 id="datasets-title">Datasets</h1>
          <p>Saved governed query results. One Dataset can support many Widgets.</p>
        </div>
        <Button type="button" onClick={() => onNavigate("ask")}>New from question</Button>
      </header>
      {datasets.length === 0 ? (
        <p className="builder-empty-note">No Datasets saved yet.</p>
      ) : (
        <div className="builder-table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Source</th><th>Columns</th><th>Rows</th><th>Saved</th></tr></thead>
            <tbody>
              {datasets.map((dataset) => {
                const source = configurationRecord(dataset, "source");
                const columns = configurationValue(dataset, "columns");
                const rowCount = configurationRecord(dataset, "rowCount");
                return (
                  <tr key={dataset.versionId}>
                    <td><strong>{entityTitle(dataset, "Dataset")}</strong></td>
                    <td>{String(source?.dataSourceId ?? "Unknown")}</td>
                    <td>{Array.isArray(columns) ? columns.length : "—"}</td>
                    <td>{String(rowCount?.returned ?? "—")}</td>
                    <td>{dateLabel(dataset.createdAt)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );

  const renderWidgets = () => (
    <section className="builder-library" aria-labelledby="widgets-title">
      <header className="builder-library__header">
        <div>
          <p className="eyebrow">Library</p>
          <h1 id="widgets-title">Widgets</h1>
          <p>Saved chart definitions backed by immutable Dataset versions.</p>
        </div>
        <Button
          type="button"
          disabled={datasets.length === 0}
          onClick={(event) => openPanel("widget", event.currentTarget)}
        >
          New Widget
        </Button>
      </header>
      {widgets.length === 0 ? (
        <p className="builder-empty-note">No Widgets saved yet.</p>
      ) : (
        <div className="builder-widget-grid">
          {widgets.map((widget) => (
            <article key={widget.versionId} className="builder-widget-card">
              <div className="builder-widget-card__preview" aria-hidden="true">▥</div>
              <h2>{entityTitle(widget, "Widget")}</h2>
              <p>{entityPresentation(widget)}</p>
              <small>Dataset {String(configurationValue(widget, "datasetVersionId")).slice(0, 8)}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );

  const renderDashboards = () => (
    <section className="builder-library" aria-labelledby="dashboards-title">
      <header className="builder-library__header">
        <div>
          <p className="eyebrow">Library</p>
          <h1 id="dashboards-title">Dashboards</h1>
          <p>Reviewed Widget collections ready for deterministic Superset import.</p>
        </div>
        <Button
          type="button"
          disabled={widgets.length === 0}
          onClick={(event) => openPanel("dashboard", event.currentTarget)}
        >
          New Dashboard
        </Button>
      </header>
      {dashboards.length === 0 ? (
        <p className="builder-empty-note">No Dashboards saved yet.</p>
      ) : (
        <div className="builder-dashboard-list">
          {dashboards.map((dashboard) => (
            <article key={dashboard.versionId} className="builder-dashboard-row">
              <div>
                <h2>{entityTitle(dashboard, "Dashboard")}</h2>
                <p>{dashboardWidgetVersionIds(dashboard).length} Widgets · saved {dateLabel(dashboard.createdAt)}</p>
              </div>
              <Button
                type="button"
                disabled={disabled || busy}
                onClick={() => void publishDashboard(dashboard)}
              >
                Publish to Superset
              </Button>
            </article>
          ))}
        </div>
      )}
      {publication && (
        <div className="builder-publication" role="status">
          <CheckmarkFilled size={20} aria-hidden="true" />
          <span>
            <strong>Superset bundle ready</strong>
            <small>{publication.pointer.bundle.fileName}</small>
          </span>
          <a href={publication.downloadPath}>Download bundle</a>
        </div>
      )}
    </section>
  );

  return (
    <>
      {activeSection === "ask" && renderAskArtifacts()}
      {activeSection === "datasets" && renderDatasets()}
      {activeSection === "widgets" && renderWidgets()}
      {activeSection === "dashboards" && renderDashboards()}

      {loading && activeSection !== "ask" && <p role="status">Loading library…</p>}
      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title="Dashboard builder action failed"
          subtitle={error}
        />
      )}

      {panel && (
        <>
          <button
            type="button"
            className="builder-review-backdrop"
            aria-label="Close review panel"
            onClick={closePanel}
          />
          <aside className="builder-review" aria-label="Review panel" aria-modal="true">
            <header className="builder-review__header">
              <div>
                <p className="eyebrow">
                  {panel === "dataset" ? "Dataset" : panel === "widget" ? "Widget" : "Dashboard"}
                </p>
                <h2>
                  {panel === "dataset"
                    ? currentDataset ? "Review saved Dataset" : "Review Dataset draft"
                    : panel === "widget"
                      ? "Review Widget draft"
                      : "Create Dashboard"}
                </h2>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                className="builder-review__close"
                aria-label="Close"
                onClick={closePanel}
              >
                <Close size={20} aria-hidden="true" />
              </button>
            </header>

            <div className="builder-review__body">
              {panel === "dataset" && session && execution && (
                <>
                  <TextInput
                    id="builder-dataset-title"
                    labelText="Dataset name"
                    value={datasetTitle}
                    disabled={busy || Boolean(currentDataset)}
                    placeholder={`Dataset from Query v${session.currentVersion?.ordinal ?? ""}`}
                    onChange={(event) => setDatasetTitle(event.currentTarget.value)}
                  />
                  {resultIsStale && (
                    <InlineNotification
                      kind="warning"
                      lowContrast
                      hideCloseButton
                      title="Result is stale"
                      subtitle="The visible editor changed after this run. Close this panel, rerun the query, and review the new result."
                    />
                  )}
                  <dl className="builder-review__metrics">
                    <div><dt>Exact query</dt><dd>Query v{session.currentVersion?.ordinal}</dd></div>
                    <div><dt>Execution</dt><dd>Run {execution.ordinal}</dd></div>
                    <div><dt>Source</dt><dd>{session.dataSourceId ?? "OpenELIS"}</dd></div>
                    <div><dt>Status</dt><dd>{execution.status}</dd></div>
                  </dl>
                  <ExecutionResult session={session} sql={sql} parameters={parameters} />
                  <details className="builder-review__sql">
                    <summary>Query v{session.currentVersion?.ordinal} SQL snapshot</summary>
                    <pre>{execution.query.sql}</pre>
                  </details>
                </>
              )}

              {panel === "widget" && (
                <>
                  <div className="builder-widget-preview" role="img" aria-label={presentationPreview(presentationKind)}>
                    <span aria-hidden="true">▥</span>
                    <p>{presentationPreview(presentationKind)}</p>
                  </div>
                  <TextInput
                    id="builder-widget-title"
                    labelText="Widget name"
                    value={widgetTitle}
                    disabled={busy}
                    placeholder="Untitled Widget"
                    onChange={(event) => setWidgetTitle(event.currentTarget.value)}
                  />
                  <Select
                    id="builder-widget-dataset"
                    labelText="Reads Dataset"
                    value={effectiveDatasetVersionId}
                    disabled={busy}
                    onChange={(event) => setSelectedDatasetVersionId(event.currentTarget.value)}
                  >
                    {datasets.map((dataset) => (
                      <SelectItem
                        key={dataset.versionId}
                        value={dataset.versionId}
                        text={entityTitle(dataset, "Dataset")}
                      />
                    ))}
                  </Select>
                  <Select
                    id="builder-presentation-kind"
                    labelText="Visualization"
                    value={presentationKind}
                    disabled={busy}
                    onChange={(event) => setPresentationKind(event.currentTarget.value as DashboardPresentationKind)}
                  >
                    {presentations.map((presentation) => (
                      <SelectItem key={presentation.value} value={presentation.value} text={presentation.label} />
                    ))}
                  </Select>
                </>
              )}

              {panel === "dashboard" && (
                <>
                  <TextInput
                    id="builder-dashboard-title"
                    labelText="Dashboard name"
                    value={dashboardTitle}
                    disabled={busy}
                    placeholder="Catalyst dashboard"
                    onChange={(event) => setDashboardTitle(event.currentTarget.value)}
                  />
                  <fieldset className="builder-widget-picker">
                    <legend>Widgets</legend>
                    {widgets.map((widget) => (
                      <label key={widget.versionId}>
                        <input
                          type="checkbox"
                          checked={selectedWidgetVersionIds.includes(widget.versionId)}
                          disabled={busy}
                          onChange={(event) => {
                            setSelectedWidgetVersionIds((current) =>
                              event.currentTarget.checked
                                ? [...current, widget.versionId]
                                : current.filter((versionId) => versionId !== widget.versionId),
                            );
                          }}
                        />
                        <span>{entityTitle(widget, "Widget")}</span>
                      </label>
                    ))}
                  </fieldset>
                </>
              )}
            </div>

            <footer className="builder-review__footer">
              {panel === "dataset" && (
                <Button
                  type="button"
                  disabled={busy || resultIsStale || Boolean(currentDataset)}
                  onClick={() => void saveDataset()}
                >
                  {currentDataset ? "Dataset saved" : busy ? "Saving…" : "Save Dataset"}
                </Button>
              )}
              {panel === "widget" && (
                <Button
                  type="button"
                  disabled={busy || !effectiveDatasetVersionId}
                  onClick={() => void saveWidget()}
                >
                  {busy ? "Saving…" : "Save Widget"}
                </Button>
              )}
              {panel === "dashboard" && (
                <Button
                  type="button"
                  disabled={busy || selectedWidgetVersionIds.length === 0}
                  onClick={() => void saveDashboard()}
                >
                  {busy ? "Saving…" : "Save Dashboard"}
                </Button>
              )}
              <Button type="button" kind="tertiary" onClick={closePanel}>Close</Button>
            </footer>
          </aside>
        </>
      )}

      {toast && (
        <div className="builder-toast" role="status">
          <CheckmarkFilled size={20} aria-hidden="true" />
          <span>{toast}</span>
          <button type="button" aria-label="Dismiss notification" onClick={() => setToast(null)}>
            <Close size={16} aria-hidden="true" />
          </button>
        </div>
      )}

      {publication && activeSection !== "dashboards" && (
        <Button
          className="builder-publication-shortcut"
          type="button"
          kind="ghost"
          renderIcon={Renew}
          onClick={() => onNavigate("dashboards")}
        >
          View published Dashboard
        </Button>
      )}
    </>
  );
};
