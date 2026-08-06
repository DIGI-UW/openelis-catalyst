import { CheckmarkFilled, Close, DataBase, Renew } from "@carbon/icons-react";
import { Button, InlineNotification, Select, SelectItem, Tag, TextInput } from "@carbon/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { CatalystApiError, type CatalystApi } from "../api";
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const recordValue = (source: Record<string, unknown> | undefined, key: string) => {
  const value = source?.[key];
  return isRecord(value) ? value : undefined;
};

const textValue = (source: Record<string, unknown> | undefined, key: string) => {
  const value = source?.[key];
  return typeof value === "string" && value ? value : undefined;
};

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
  session?.executions
    .filter((execution) => execution.status === "succeeded")
    .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null;

const displayParameterValue = (value: BoundParameter["value"]) =>
  Array.isArray(value) ? JSON.stringify(value) : String(value);

const presentationPreview = (kind: DashboardPresentationKind) => {
  if (kind === "table") return "Table preview using every returned column";
  if (kind === "big_number") return "Single-value KPI preview";
  if (kind.startsWith("time_series")) return "Time-series preview using the temporal and numeric columns";
  return "Bar chart preview using the categorical and numeric columns";
};

interface PresentationAssessment {
  kind: DashboardPresentationKind;
  compatible: boolean;
  reason: string;
}

const datasetColumns = (dataset: DashboardBuilderEntity | null) => {
  const columns = dataset ? configurationValue(dataset, "columns") : null;
  return Array.isArray(columns) ? columns.filter(isRecord) : [];
};

const presentationAssessments = (
  dataset: DashboardBuilderEntity | null,
): PresentationAssessment[] => {
  const columns = datasetColumns(dataset);
  const rowCount = Number(
    (dataset ? configurationRecord(dataset, "rowCount") : null)?.returned ?? 0,
  );
  const numeric = columns.filter((column) =>
    ["integer", "decimal"].includes(String(column.logicalType)),
  );
  const temporal = columns.filter((column) =>
    ["date", "date-time"].includes(String(column.logicalType)),
  );
  const categorical = columns.filter((column) =>
    ["string", "boolean"].includes(String(column.logicalType)),
  );
  const compatible = (kind: DashboardPresentationKind) => {
    if (kind === "table") return true;
    if (kind === "big_number") return rowCount === 1 && columns.length === 1 && numeric.length === 1;
    if (kind === "time_series_line" || kind === "time_series_area") {
      return temporal.length > 0 && numeric.length > 0;
    }
    if (kind === "proportion_bar") return categorical.length > 1 && numeric.length > 0;
    return categorical.length > 0 && numeric.length > 0;
  };
  const reason = (kind: DashboardPresentationKind) => {
    if (kind === "table") return "Table supports every returned schema.";
    if (kind === "big_number") return "Big number requires exactly one returned numeric cell.";
    if (kind === "time_series_line" || kind === "time_series_area") {
      return `${presentations.find((item) => item.value === kind)?.label} requires a temporal and numeric column.`;
    }
    if (kind === "proportion_bar") {
      return "100% stacked bar requires two categorical columns and a numeric column.";
    }
    return `${presentations.find((item) => item.value === kind)?.label} requires a categorical and numeric column.`;
  };
  return presentations.map(({ value }) => ({
    kind: value,
    compatible: compatible(value),
    reason: reason(value),
  }));
};

const suggestedPresentation = (dataset: DashboardBuilderEntity | null) => {
  const assessments = presentationAssessments(dataset);
  const columns = datasetColumns(dataset);
  const rowCount = Number(
    (dataset ? configurationRecord(dataset, "rowCount") : null)?.returned ?? 0,
  );
  const numeric = columns.filter((column) =>
    ["integer", "decimal"].includes(String(column.logicalType)),
  );
  const temporal = columns.some((column) =>
    ["date", "date-time"].includes(String(column.logicalType)),
  );
  const categorical = columns.some((column) => String(column.logicalType) === "string");
  if (columns.length === 1 && numeric.length === 1 && rowCount === 1) return "big_number";
  if (temporal && numeric.length > 0) return "time_series_line";
  if (categorical && numeric.length > 0 && columns.length <= 4) return "grouped_bar";
  return assessments.find((assessment) => assessment.compatible)?.kind ?? "table";
};

const presentationBindingSummary = (
  dataset: DashboardBuilderEntity | null,
  kind: DashboardPresentationKind,
) => {
  const columns = datasetColumns(dataset);
  const names = (items: Record<string, unknown>[]) =>
    items.map((column) => String(column.name ?? "unnamed")).join(", ");
  const numeric = columns.filter((column) =>
    ["integer", "decimal"].includes(String(column.logicalType)),
  );
  const temporal = columns.filter((column) =>
    ["date", "date-time"].includes(String(column.logicalType)),
  );
  const categorical = columns.filter((column) =>
    ["string", "boolean"].includes(String(column.logicalType)),
  );
  if (kind === "table") return `Columns: ${names(columns) || "none"}`;
  if (kind === "big_number") return `Metric: ${names(numeric.slice(0, 1))}`;
  if (kind === "time_series_line" || kind === "time_series_area") {
    return `Time: ${names(temporal.slice(0, 1))} · Metric: ${names(numeric.slice(0, 1))}`;
  }
  return `Category: ${names(categorical.slice(0, 1))} · Metric: ${names(numeric.slice(0, 1))}`;
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

const hasExactImportedEvidence = (publication: DashboardPublication | undefined) =>
  publication?.status === "imported" &&
  publication.importState?.outcome === "imported" &&
  Boolean(publication.importState.receiptId) &&
  Boolean(publication.importState.receiptDigest) &&
  Boolean(publication.importState.dashboardUrl);

const publicationFailureGuidance = (
  publication: DashboardPublication,
  importEvidenceIncomplete: boolean,
) => {
  if (importEvidenceIncomplete) {
    return "The import receipt does not exactly match this Dashboard version. Run the local Superset status/import helper for this exact bundle, then reload.";
  }
  if (
    publication.importState?.recoveryAction ===
    "full_reset_then_reimport_last_verified_bundle"
  ) {
    return "Stop the local Superset stack, fully reset only its metadata database and home volumes, then reimport and verify this Dashboard's last-verified bundle. Do not delete individual assets.";
  }
  return "Run the local Superset import helper again for this exact bundle, then reload this page.";
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
  const [hydratedDatasetSession, setHydratedDatasetSession] = useState<{
    datasetVersionId: string;
    session: WorkbenchSession;
  } | null>(null);
  const [datasetEvidenceLoading, setDatasetEvidenceLoading] = useState(false);
  const [widgetTitle, setWidgetTitle] = useState("");
  const [dashboardTitle, setDashboardTitle] = useState("");
  const [presentationKind, setPresentationKind] =
    useState<DashboardPresentationKind>("table");
  const [selectedDatasetVersionId, setSelectedDatasetVersionId] = useState("");
  const [reviewedDatasetVersionId, setReviewedDatasetVersionId] = useState("");
  const [selectedWidgetVersionIds, setSelectedWidgetVersionIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(
    Boolean(api.listDashboardDatasets && api.listDashboardWidgets && api.listDashboards),
  );
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [publication, setPublication] = useState<DashboardPublication | null>(null);
  const [publicationsByVersion, setPublicationsByVersion] = useState<
    Record<string, DashboardPublication>
  >({});
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const reviewRef = useRef<HTMLElement | null>(null);
  const [returnFocusTarget, setReturnFocusTarget] = useState<HTMLElement | null>(null);

  const execution = newestSuccessfulExecution(session);
  const executionVersion = execution
    ? session?.versions.find((version) => version.versionId === execution.versionId) ?? null
    : null;
  const executionVersionOrdinal = executionVersion?.ordinal ?? "?";
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
  const resultIsStale = Boolean(
    execution &&
      (!editorMatchesCurrent ||
        execution.versionId !== session?.currentVersion?.versionId),
  );
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

  const reviewedDataset = reviewedDatasetVersionId
    ? datasets.find((candidate) => candidate.versionId === reviewedDatasetVersionId) ?? null
    : currentDataset;
  const reviewedDatasetSource = reviewedDataset
    ? configurationRecord(reviewedDataset, "source")
    : null;
  const reviewedSession = reviewedDatasetSource
    ? reviewedDatasetSource.sessionId === session?.sessionId
      ? session
      : hydratedDatasetSession &&
          hydratedDatasetSession.datasetVersionId === reviewedDataset?.versionId
        ? hydratedDatasetSession.session
        : null
    : session;
  const reviewedExecution = reviewedDatasetSource && reviewedSession
    ? reviewedSession.executions.find(
        (candidate) =>
          candidate.executionId === reviewedDatasetSource.executionId,
      ) ?? null
    : execution;
  const reviewedVersion = reviewedExecution && reviewedSession
    ? reviewedSession.versions.find((version) => version.versionId === reviewedExecution.versionId) ?? null
    : null;
  const reviewedValidation = reviewedExecution && reviewedSession
    ? (reviewedSession.validations ?? [])
        .filter(
          (validation) =>
            validation.versionId === reviewedExecution.versionId &&
            validation.queryDigest === reviewedExecution.queryDigest,
        )
        .sort((left, right) => right.ordinal - left.ordinal)[0] ?? null
    : null;
  const reviewedProvenance = reviewedVersion?.provenance;
  const sessionProvenance = reviewedSession?.provenance;
  const profileSnapshot =
    recordValue(reviewedProvenance, "profileSnapshot") ??
    (recordValue(reviewedProvenance, "roleModels") ? reviewedProvenance : undefined) ??
    recordValue(sessionProvenance, "profileSnapshot");
  const roleModels = recordValue(profileSnapshot, "roleModels");
  const profileLabel =
    textValue(profileSnapshot, "profileLabel") ??
    textValue(profileSnapshot, "profileName") ??
    textValue(reviewedProvenance, "profileLabel") ??
    reviewedSession?.profileId ??
    "Unknown profile";
  const catalystTraceId =
    textValue(reviewedProvenance, "catalystTraceId") ??
    textValue(sessionProvenance, "catalystTraceId");
  const hubTraceId =
    textValue(reviewedProvenance, "hubTraceId") ??
    textValue(sessionProvenance, "hubTraceId");

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
        if (api.getDashboardPublication) {
          void Promise.all(
            dashboardCollection.items.map(async (dashboard) => {
              try {
                return [
                  dashboard.versionId,
                  await api.getDashboardPublication!(dashboard.versionId, controller.signal),
                ] as const;
              } catch (caught) {
                if (caught instanceof CatalystApiError && caught.status === 404) {
                  return null;
                }
                throw caught;
              }
            }),
          )
            .then((entries) => {
              if (controller.signal.aborted) return;
              setPublicationsByVersion(
                Object.fromEntries(entries.filter((entry) => entry !== null)),
              );
            })
            .catch((caught: unknown) => {
              if (!controller.signal.aborted) {
                setError(
                  caught instanceof Error
                    ? caught.message
                    : "Catalyst could not load Superset publication state.",
                );
              }
            });
        }
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
  const selectedDataset =
    datasets.find((candidate) => candidate.versionId === effectiveDatasetVersionId) ?? null;
  const selectedPresentationAssessments = presentationAssessments(selectedDataset);
  const compatiblePresentations = selectedPresentationAssessments.filter(
    (assessment) => assessment.compatible,
  );
  const suggestedKind = suggestedPresentation(selectedDataset);

  useEffect(() => {
    if (!panel) return;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPanel(null);
        window.setTimeout(() => returnFocusTarget?.focus(), 0);
        return;
      }
      if (event.key !== "Tab" || !reviewRef.current) return;
      const focusable = Array.from(
        reviewRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [panel, returnFocusTarget]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const openPanel = (
    next: Exclude<ReviewPanel, null>,
    entityVersionId?: string,
  ) => {
    setError(null);
    setPublication(null);
    if (next === "dataset") {
      const selected = entityVersionId
        ? datasets.find((candidate) => candidate.versionId === entityVersionId) ?? null
        : currentDataset;
      setReviewedDatasetVersionId(selected?.versionId ?? "");
      setDatasetTitle(selected ? entityTitle(selected, "Dataset") : "");
      setHydratedDatasetSession(null);
      setDatasetEvidenceLoading(false);
      const source = selected ? configurationRecord(selected, "source") : null;
      const sourceSessionId =
        typeof source?.sessionId === "string" ? source.sessionId : null;
      if (
        selected &&
        sourceSessionId &&
        sourceSessionId !== session?.sessionId &&
        api.getWorkbenchSession
      ) {
        setDatasetEvidenceLoading(true);
        void api.getWorkbenchSession(sourceSessionId)
          .then((sourceSession) => {
            setHydratedDatasetSession({
              datasetVersionId: selected.versionId,
              session: sourceSession,
            });
          })
          .catch((caught: unknown) => {
            setError(
              caught instanceof Error
                ? caught.message
                : "Catalyst could not load the Dataset's source execution.",
            );
          })
          .finally(() => setDatasetEvidenceLoading(false));
      }
    }
    if (next === "widget") {
      const datasetVersionId = currentDataset?.versionId ?? datasets[0]?.versionId ?? "";
      const dataset =
        datasets.find((candidate) => candidate.versionId === datasetVersionId) ?? null;
      setSelectedDatasetVersionId(datasetVersionId);
      setPresentationKind(suggestedPresentation(dataset));
    }
    if (next === "dashboard") {
      setDashboardTitle("");
      setSelectedWidgetVersionIds(
        entityVersionId
          ? [entityVersionId]
          : widgets[0]
            ? [widgets[0].versionId]
            : [],
      );
    }
    setPanel(next);
  };

  const closePanel = () => {
    setPanel(null);
    window.setTimeout(() => returnFocusTarget?.focus(), 0);
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
      setPublicationsByVersion((current) => ({
        ...current,
        [dashboard.versionId]: result,
      }));
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
          onClick={(event) => {
            setReturnFocusTarget(event.currentTarget);
            openPanel("dataset", currentDataset?.versionId);
          }}
          aria-label="Review dataset draft"
        >
          <DataBase size={20} aria-hidden="true" />
          <span>
            <strong>{currentDataset ? entityTitle(currentDataset, "Saved Dataset") : `Dataset from Query v${executionVersionOrdinal}`}</strong>
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
            onClick={(event) => {
              setReturnFocusTarget(event.currentTarget);
              openPanel("widget");
            }}
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
            <thead><tr><th>Name</th><th>Source</th><th>Columns</th><th>Rows</th><th>Parameters</th><th>Widgets</th><th>Status</th><th>Saved</th><th>Action</th></tr></thead>
            <tbody>
              {datasets.map((dataset) => {
                const source = configurationRecord(dataset, "source");
                const columns = configurationValue(dataset, "columns");
                const rowCount = configurationRecord(dataset, "rowCount");
                const savedParameters = configurationValue(dataset, "parameters");
                const widgetCount = widgets.filter(
                  (widget) =>
                    configurationValue(widget, "datasetVersionId") === dataset.versionId,
                ).length;
                return (
                  <tr key={dataset.versionId}>
                    <td><strong>{entityTitle(dataset, "Dataset")}</strong></td>
                    <td>{String(source?.dataSourceId ?? "Unknown")}</td>
                    <td>{Array.isArray(columns) ? columns.length : "—"}</td>
                    <td>{String(rowCount?.returned ?? "—")}</td>
                    <td>{Array.isArray(savedParameters) ? savedParameters.length : "—"}</td>
                    <td>{widgetCount}</td>
                    <td><Tag type="green">Ready</Tag></td>
                    <td>{dateLabel(dataset.createdAt)}</td>
                    <td>
                      <Button
                        type="button"
                        kind="ghost"
                        size="sm"
                        onClick={(event) => {
                          setReturnFocusTarget(event.currentTarget);
                          openPanel("dataset", dataset.versionId);
                        }}
                      >
                        Review {entityTitle(dataset, "Dataset")}
                      </Button>
                    </td>
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
          onClick={(event) => {
            setReturnFocusTarget(event.currentTarget);
            openPanel("widget");
          }}
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
              <Button
                type="button"
                kind="ghost"
                size="sm"
                className="builder-widget-card__action"
                onClick={(event) => {
                  setReturnFocusTarget(event.currentTarget);
                  openPanel("dashboard", widget.versionId);
                }}
              >
                Add {entityTitle(widget, "Widget")} to dashboard
              </Button>
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
          onClick={(event) => {
            setReturnFocusTarget(event.currentTarget);
            openPanel("dashboard");
          }}
        >
          New Dashboard
        </Button>
      </header>
      {dashboards.length === 0 ? (
        <p className="builder-empty-note">No Dashboards saved yet.</p>
      ) : (
        <div className="builder-dashboard-list">
          {dashboards.map((dashboard) => {
            const savedPublication = publicationsByVersion[dashboard.versionId];
            const exactImported = hasExactImportedEvidence(savedPublication);
            const importEvidenceIncomplete = Boolean(
              savedPublication?.status === "imported" && !exactImported,
            );
            const displayStatus = importEvidenceIncomplete
              ? "import_failed"
              : savedPublication?.status;
            return (
              <article key={dashboard.versionId} className="builder-dashboard-row">
                <div>
                  <h2>{entityTitle(dashboard, "Dashboard")}</h2>
                  <p>{dashboardWidgetVersionIds(dashboard).length} Widgets · saved {dateLabel(dashboard.createdAt)}</p>
                  {displayStatus === "imported" && <Tag type="green">Imported</Tag>}
                  {displayStatus === "bundle_ready" && <Tag type="blue">Superset bundle ready</Tag>}
                  {displayStatus === "import_failed" && savedPublication && (
                    <>
                      <Tag type="red">Import failed</Tag>
                      <small className="builder-dashboard-row__diagnostic">
                        {savedPublication.importState?.errorCode ??
                          (importEvidenceIncomplete ? "import_evidence_incomplete" : "import_failed")}
                        {" — "}
                        {publicationFailureGuidance(
                          savedPublication,
                          importEvidenceIncomplete,
                        )}
                      </small>
                    </>
                  )}
                </div>
                <div className="builder-dashboard-row__actions">
                  {exactImported && savedPublication?.importState?.dashboardUrl ? (
                    <Button
                      as="a"
                      kind="primary"
                      href={savedPublication.importState.dashboardUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Superset
                    </Button>
                  ) : !savedPublication || displayStatus === "import_failed" ? (
                    <Button
                      type="button"
                      disabled={disabled || busy}
                      onClick={() => void publishDashboard(dashboard)}
                    >
                      Publish to Superset
                    </Button>
                  ) : null}
                  {savedPublication && (
                    <a href={savedPublication.downloadPath}>Download bundle</a>
                  )}
                </div>
              </article>
            );
          })}
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
            tabIndex={-1}
            onClick={closePanel}
          />
          <aside
            ref={reviewRef}
            className="builder-review"
            role="dialog"
            aria-label="Review panel"
            aria-modal="true"
          >
            <header className="builder-review__header">
              <div>
                <p className="eyebrow">
                  {panel === "dataset" ? "Dataset" : panel === "widget" ? "Widget" : "Dashboard"}
                </p>
                <h2>
                  {panel === "dataset"
                    ? reviewedDataset ? "Review saved Dataset" : "Review Dataset draft"
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
              {panel === "dataset" && reviewedSession && reviewedExecution && (
                <>
                  <TextInput
                    id="builder-dataset-title"
                    labelText="Dataset name"
                    value={datasetTitle}
                    disabled={busy || Boolean(reviewedDataset)}
                    placeholder={`Dataset from Query v${reviewedVersion?.ordinal ?? "?"}`}
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
                    <div><dt>Exact query</dt><dd>Query v{reviewedVersion?.ordinal ?? "?"}</dd></div>
                    <div><dt>Execution</dt><dd>Run {reviewedExecution.ordinal}</dd></div>
                    <div><dt>Source</dt><dd>{reviewedSession.dataSourceId ?? "OpenELIS"}</dd></div>
                    <div><dt>Status</dt><dd>{reviewedExecution.status}</dd></div>
                    <div><dt>Catalog</dt><dd>{reviewedSession.catalogVersion ?? "Unknown"}</dd></div>
                    <div><dt>Profile</dt><dd>{profileLabel}</dd></div>
                    {roleModels && Object.entries(roleModels)
                      .filter((entry): entry is [string, string] => typeof entry[1] === "string")
                      .sort(([left], [right]) => left.localeCompare(right))
                      .map(([role, model]) => (
                        <div key={role}><dt>{role.replaceAll("_", " ")}</dt><dd>{model}</dd></div>
                      ))}
                    {catalystTraceId && <div><dt>Catalyst trace</dt><dd>{catalystTraceId}</dd></div>}
                    {hubTraceId && <div><dt>Hub trace</dt><dd>{hubTraceId}</dd></div>}
                    <div>
                      <dt>Database diagnostic</dt>
                      <dd>
                        {reviewedExecution.databaseDiagnostic?.message ??
                          (reviewedExecution.status === "succeeded"
                            ? "None — run succeeded"
                            : "Unavailable")}
                      </dd>
                    </div>
                  </dl>
                  <section className="builder-review__evidence" aria-labelledby="dataset-parameters-title">
                    <h3 id="dataset-parameters-title">Typed parameters</h3>
                    {reviewedExecution.query.parameters.length === 0 ? (
                      <p>No bound parameters.</p>
                    ) : (
                      <dl>
                        {reviewedExecution.query.parameters.map((parameter) => (
                          <div key={parameter.name}>
                            <dt>:{parameter.name}</dt>
                            <dd>{parameter.type}</dd>
                            <dd>{displayParameterValue(parameter.value)}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </section>
                  <section className="builder-review__evidence" aria-labelledby="dataset-findings-title">
                    <h3 id="dataset-findings-title">Validation findings</h3>
                    {!reviewedValidation || reviewedValidation.findings.length === 0 ? (
                      <p>No validation findings recorded for this exact query.</p>
                    ) : (
                      <ul>
                        {reviewedValidation.findings.map((finding) => (
                          <li key={finding.findingId}>
                            <strong>{finding.ruleCode}</strong> — {finding.message}
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                  <ExecutionResult
                    session={reviewedSession}
                    sql={reviewedDataset ? reviewedExecution.query.sql : sql}
                    parameters={
                      reviewedDataset ? reviewedExecution.query.parameters : parameters
                    }
                    executionOverride={reviewedExecution}
                    pageSize={25}
                  />
                  <details className="builder-review__sql">
                    <summary>Query v{reviewedVersion?.ordinal ?? "?"} SQL snapshot</summary>
                    <pre>{reviewedExecution.query.sql}</pre>
                  </details>
                </>
              )}
              {panel === "dataset" && datasetEvidenceLoading && (
                <p role="status">Loading exact Dataset execution evidence…</p>
              )}
              {panel === "dataset" && !datasetEvidenceLoading && (!reviewedSession || !reviewedExecution) && (
                <InlineNotification
                  kind="warning"
                  lowContrast
                  hideCloseButton
                  title="Execution evidence is unavailable in this session"
                  subtitle="Return to the source query session to review its typed rows and exact execution evidence."
                />
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
                    onChange={(event) => {
                      const datasetVersionId = event.currentTarget.value;
                      const dataset =
                        datasets.find((candidate) => candidate.versionId === datasetVersionId) ?? null;
                      setSelectedDatasetVersionId(datasetVersionId);
                      setPresentationKind(suggestedPresentation(dataset));
                    }}
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
                    {compatiblePresentations.map((assessment) => {
                      const presentation = presentations.find(
                        (candidate) => candidate.value === assessment.kind,
                      )!;
                      return (
                      <SelectItem key={presentation.value} value={presentation.value} text={presentation.label} />
                      );
                    })}
                  </Select>
                  <section className="builder-review__evidence" aria-labelledby="widget-binding-title">
                    <h3 id="widget-binding-title">Chart binding</h3>
                    <p>
                      Suggested: {presentations.find((item) => item.value === suggestedKind)?.label}
                    </p>
                    <p>{presentationBindingSummary(selectedDataset, presentationKind)}</p>
                  </section>
                  <section className="builder-review__evidence" aria-labelledby="widget-compatibility-title">
                    <h3 id="widget-compatibility-title">Compatibility</h3>
                    <ul>
                      {selectedPresentationAssessments
                        .filter((assessment) => !assessment.compatible)
                        .map((assessment) => (
                          <li key={assessment.kind}>{assessment.reason}</li>
                        ))}
                    </ul>
                  </section>
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
                            const checked = event.currentTarget.checked;
                            setSelectedWidgetVersionIds((current) =>
                              checked
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
                  disabled={busy || resultIsStale || Boolean(reviewedDataset) || !reviewedExecution || datasetEvidenceLoading}
                  onClick={() => void saveDataset()}
                >
                  {reviewedDataset ? "Dataset saved" : busy ? "Saving…" : "Save Dataset"}
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
