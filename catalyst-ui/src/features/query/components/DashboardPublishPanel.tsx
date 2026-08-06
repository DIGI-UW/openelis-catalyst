import {
  Button,
  InlineNotification,
  Select,
  SelectItem,
  TextInput,
} from "@carbon/react";
import { useEffect, useState } from "react";
import type { CatalystApi } from "../api";
import type {
  DashboardBuilderEntity,
  DashboardPresentationKind,
  DashboardPublication,
  WorkbenchExecution,
  WorkbenchSession,
} from "../types";
import "./DashboardPublishPanel.css";

interface DashboardPublishPanelProps {
  api: CatalystApi;
  session: WorkbenchSession;
  disabled?: boolean;
}

const presentations: Array<{ value: DashboardPresentationKind; label: string }> = [
  { value: "table", label: "Table" },
  { value: "big_number", label: "Big number" },
  { value: "time_series_line", label: "Time-series line" },
  { value: "time_series_area", label: "Time-series area" },
  { value: "grouped_bar", label: "Grouped bar" },
  { value: "stacked_bar", label: "Stacked bar" },
  { value: "proportion_bar", label: "100% stacked bar" },
];

const newestSuccessfulExecution = (session: WorkbenchSession) =>
  session.currentVersion
    ? session.executions
      .filter(
        (execution) =>
          execution.status === "succeeded" &&
          execution.versionId === session.currentVersion?.versionId,
      )
      .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null
    : null;

const publicationMessage = (publication: DashboardPublication) =>
  `Bundle ${publication.pointer.bundle.fileName} is ready in the local Superset outbox.`;

const configurationValue = (entity: DashboardBuilderEntity, key: string) =>
  entity.configuration[key];

const configurationRecord = (entity: DashboardBuilderEntity, key: string) => {
  const value = configurationValue(entity, key);
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
};

const entityTitle = (entity: DashboardBuilderEntity) => {
  const title = configurationValue(entity, "title");
  return typeof title === "string" && title.trim() ? title : "Untitled widget";
};

const entityPresentation = (entity: DashboardBuilderEntity) => {
  const kind = configurationValue(entity, "presentationKind");
  return presentations.find((presentation) => presentation.value === kind)?.label ?? "Widget";
};

interface DashboardPublishPanelContentProps extends DashboardPublishPanelProps {
  execution: WorkbenchExecution | null;
}

const DashboardPublishPanelContent = ({
  api,
  session,
  execution,
  disabled = false,
}: DashboardPublishPanelContentProps) => {
  const [dashboardTitle, setDashboardTitle] = useState("");
  const [widgetTitle, setWidgetTitle] = useState("");
  const [presentationKind, setPresentationKind] =
    useState<DashboardPresentationKind>("table");
  const [dataset, setDataset] = useState<DashboardBuilderEntity | null>(null);
  const [widgets, setWidgets] = useState<DashboardBuilderEntity[]>([]);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(
    Boolean(execution && api.listDashboardDatasets && api.listDashboardWidgets),
  );
  const [publication, setPublication] = useState<DashboardPublication | null>(null);
  const [error, setError] = useState<string | null>(null);
  const supported = Boolean(
    api.saveDashboardDataset &&
      api.saveDashboardWidget &&
      api.saveDashboard &&
      api.publishDashboard,
  );
  const canAddWidget = Boolean(execution && supported);

  useEffect(() => {
    if (
      !execution ||
      !api.listDashboardDatasets ||
      !api.listDashboardWidgets
    ) {
      return;
    }

    const controller = new AbortController();
    Promise.all([
      api.listDashboardDatasets(controller.signal),
      api.listDashboardWidgets(controller.signal),
    ])
      .then(([datasetCollection, widgetCollection]) => {
        const matchingDataset = datasetCollection.items.find((candidate) => {
          const source = configurationRecord(candidate, "source");
          return (
            source?.sessionId === session.sessionId &&
            source?.executionId === execution.executionId
          );
        });
        if (!matchingDataset) return;
        setDataset(matchingDataset);
        setWidgets(
          widgetCollection.items
            .filter(
              (candidate) =>
                configurationValue(candidate, "datasetVersionId") ===
                matchingDataset.versionId,
            )
            .sort((left, right) => left.createdAt.localeCompare(right.createdAt)),
        );
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Catalyst could not restore the saved dashboard draft.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setRestoring(false);
      });

    return () => controller.abort();
  }, [api, execution, session.sessionId]);

  const addWidget = async () => {
    if (!execution || !supported || busy || !canAddWidget) return;

    setBusy(true);
    setError(null);
    setPublication(null);
    try {
      const savedDataset =
        dataset ??
        (await api.saveDashboardDataset!({
          sessionId: session.sessionId,
          executionId: execution.executionId,
        }));
      if (!dataset) setDataset(savedDataset);

      const widget = await api.saveDashboardWidget!({
        datasetVersionId: savedDataset.versionId,
        ...(widgetTitle.trim() ? { title: widgetTitle.trim() } : {}),
        presentationKind,
      });
      setWidgets((current) => [...current, widget]);
      setWidgetTitle("");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Catalyst could not save this dashboard widget.",
      );
    } finally {
      setBusy(false);
    }
  };

  const createBundle = async () => {
    if (!supported || busy || widgets.length === 0) return;

    setBusy(true);
    setError(null);
    setPublication(null);
    try {
      const dashboard = await api.saveDashboard!({
        widgetVersionIds: widgets.map((widget) => widget.versionId),
        ...(dashboardTitle.trim() ? { title: dashboardTitle.trim() } : {}),
      });
      setPublication(await api.publishDashboard!(dashboard.versionId));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Catalyst could not create the Superset bundle.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="dashboard-publish" aria-labelledby="dashboard-publish-title">
      <div>
        <h2 id="dashboard-publish-title">Dashboard builder</h2>
        <p>
          Turn the latest successful query into reviewed Superset widgets based on its saved
          governed SQL.
        </p>
      </div>

      {!supported ? (
        <InlineNotification
          kind="info"
          lowContrast
          hideCloseButton
          title="Dashboard export is unavailable in this deployment."
        />
      ) : !execution ? (
        <InlineNotification
          kind="info"
          lowContrast
          hideCloseButton
          title="Run the current query before creating dashboard widgets."
          subtitle="Saving an older or stale result is intentionally not allowed."
        />
      ) : (
        <div className="dashboard-publish__controls">
          <TextInput
            id="dashboard-title"
            labelText="Dashboard title"
            placeholder="Optional title"
            value={dashboardTitle}
            disabled={disabled || busy || restoring}
            onChange={(event) => setDashboardTitle(event.currentTarget.value)}
          />
          <TextInput
            id="dashboard-widget-title"
            labelText="Widget title"
            placeholder="Optional title"
            value={widgetTitle}
            disabled={disabled || busy || restoring}
            onChange={(event) => setWidgetTitle(event.currentTarget.value)}
          />
          <Select
            id="dashboard-presentation-kind"
            labelText="Visualization"
            value={presentationKind}
            disabled={disabled || busy || restoring}
            onChange={(event) =>
              setPresentationKind(event.currentTarget.value as DashboardPresentationKind)
            }
          >
            {presentations.map((presentation) => (
              <SelectItem
                key={presentation.value}
                value={presentation.value}
                text={presentation.label}
              />
            ))}
          </Select>
          <Button
            disabled={disabled || busy || restoring || !canAddWidget}
            onClick={() => void addWidget()}
          >
            {restoring ? "Restoring…" : busy ? "Saving widget…" : "Add widget"}
          </Button>
          <div className="dashboard-publish__widget-count" aria-live="polite">
            {widgets.length} {widgets.length === 1 ? "widget" : "widgets"} ready
          </div>
          {widgets.length > 0 && (
            <ul className="dashboard-publish__widgets" aria-label="Dashboard widgets">
              {widgets.map((widget) => (
                <li key={widget.versionId}>
                  <span>
                    <strong>{entityTitle(widget)}</strong>
                    <small>{entityPresentation(widget)}</small>
                  </span>
                  <Button
                    kind="ghost"
                    size="sm"
                    disabled={disabled || busy || restoring}
                    onClick={() =>
                      setWidgets((current) =>
                        current.filter((candidate) => candidate.versionId !== widget.versionId),
                      )
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <Button
            kind="secondary"
            disabled={disabled || busy || restoring || widgets.length === 0}
            onClick={() => void createBundle()}
          >
            {busy ? "Publishing…" : "Publish to Superset"}
          </Button>
        </div>
      )}

      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title="Dashboard bundle was not created"
          subtitle={error}
        />
      )}
      {publication && (
        <div className="dashboard-publish__ready">
          <InlineNotification
            kind="success"
            lowContrast
            hideCloseButton
            title="Superset bundle ready"
            subtitle={publicationMessage(publication)}
          />
          <a href={publication.downloadPath}>Download bundle</a>
        </div>
      )}
    </section>
  );
};

export const DashboardPublishPanel = (props: DashboardPublishPanelProps) => {
  const execution = newestSuccessfulExecution(props.session);

  return (
    <DashboardPublishPanelContent
      key={`${props.session.sessionId}:${execution?.executionId ?? "none"}`}
      {...props}
      execution={execution}
    />
  );
};
