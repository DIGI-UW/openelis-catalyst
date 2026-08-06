import {
  Button,
  InlineNotification,
  Select,
  SelectItem,
  TextInput,
} from "@carbon/react";
import { useState } from "react";
import type { CatalystApi } from "../api";
import type {
  DashboardAggregation,
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

const aggregations: Array<{ value: DashboardAggregation; label: string }> = [
  { value: "sum", label: "Sum" },
  { value: "avg", label: "Average" },
  { value: "min", label: "Minimum" },
  { value: "max", label: "Maximum" },
  { value: "count", label: "Count (non-null values)" },
  { value: "count_distinct", label: "Distinct count" },
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
  const [aggregation, setAggregation] = useState<DashboardAggregation | "">("");
  const [dataset, setDataset] = useState<DashboardBuilderEntity | null>(null);
  const [widgets, setWidgets] = useState<DashboardBuilderEntity[]>([]);
  const [busy, setBusy] = useState(false);
  const [publication, setPublication] = useState<DashboardPublication | null>(null);
  const [error, setError] = useState<string | null>(null);
  const supported = Boolean(
    api.saveDashboardDataset &&
      api.saveDashboardWidget &&
      api.saveDashboard &&
      api.publishDashboard,
  );
  const table = presentationKind === "table";
  const canAddWidget = Boolean(execution && supported && (table || aggregation));

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
        ...(table ? {} : { aggregation: aggregation as DashboardAggregation }),
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
          Turn the latest successful query into reviewed Superset widgets. Charts use the
          aggregation you select against the saved governed SQL.
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
            disabled={disabled || busy}
            onChange={(event) => setDashboardTitle(event.currentTarget.value)}
          />
          <TextInput
            id="dashboard-widget-title"
            labelText="Widget title"
            placeholder="Optional title"
            value={widgetTitle}
            disabled={disabled || busy}
            onChange={(event) => setWidgetTitle(event.currentTarget.value)}
          />
          <Select
            id="dashboard-presentation-kind"
            labelText="Visualization"
            value={presentationKind}
            disabled={disabled || busy}
            onChange={(event) => {
              setPresentationKind(event.currentTarget.value as DashboardPresentationKind);
              setAggregation("");
            }}
          >
            {presentations.map((presentation) => (
              <SelectItem
                key={presentation.value}
                value={presentation.value}
                text={presentation.label}
              />
            ))}
          </Select>
          {!table && (
            <Select
              id="dashboard-aggregation"
              labelText="Aggregation"
              value={aggregation}
              disabled={disabled || busy}
              onChange={(event) =>
                setAggregation(event.currentTarget.value as DashboardAggregation)
              }
            >
              <SelectItem value="" text="Select aggregation" disabled />
              {aggregations.map((option) => (
                <SelectItem key={option.value} value={option.value} text={option.label} />
              ))}
            </Select>
          )}
          <Button
            disabled={disabled || busy || !canAddWidget}
            onClick={() => void addWidget()}
          >
            {busy ? "Saving widget…" : "Add widget"}
          </Button>
          <div className="dashboard-publish__widget-count" aria-live="polite">
            {widgets.length} {widgets.length === 1 ? "widget" : "widgets"} ready
          </div>
          <Button
            kind="secondary"
            disabled={disabled || busy || widgets.length === 0}
            onClick={() => void createBundle()}
          >
            {busy ? "Creating bundle…" : "Create Superset bundle"}
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
