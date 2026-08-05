import { Button, InlineNotification, TextInput } from "@carbon/react";
import { useState } from "react";
import type { CatalystApi } from "../api";
import type {
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

export const DashboardPublishPanel = ({
  api,
  session,
  disabled = false,
}: DashboardPublishPanelProps) => {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [publication, setPublication] = useState<DashboardPublication | null>(null);
  const [error, setError] = useState<string | null>(null);
  const execution: WorkbenchExecution | null = newestSuccessfulExecution(session);
  const supported = Boolean(
    api.saveDashboardDataset &&
      api.saveDashboardWidget &&
      api.saveDashboard &&
      api.publishDashboard,
  );

  const createBundle = async () => {
    if (!execution || !supported || busy) return;

    setBusy(true);
    setError(null);
    setPublication(null);
    try {
      const dataset = await api.saveDashboardDataset!({
        sessionId: session.sessionId,
        executionId: execution.executionId,
        ...(title.trim() ? { title: title.trim() } : {}),
      });
      const widget = await api.saveDashboardWidget!({
        datasetVersionId: dataset.versionId,
        ...(title.trim() ? { title: title.trim() } : {}),
      });
      const dashboard = await api.saveDashboard!({
        widgetVersionIds: [widget.versionId],
        ...(title.trim() ? { title: title.trim() } : {}),
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
        <h2 id="dashboard-publish-title">Dashboard</h2>
        <p>
          Create a native Superset bundle from the current query’s latest successful run.
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
          title="Run the current query before creating a dashboard."
          subtitle="Saving an older or stale result is intentionally not allowed."
        />
      ) : (
        <div className="dashboard-publish__controls">
          <TextInput
            id="dashboard-title"
            labelText="Dashboard title"
            placeholder="Optional title"
            value={title}
            disabled={disabled || busy}
            onChange={(event) => setTitle(event.currentTarget.value)}
          />
          <Button disabled={disabled || busy} onClick={() => void createBundle()}>
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
