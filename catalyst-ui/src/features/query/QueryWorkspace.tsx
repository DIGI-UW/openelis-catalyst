import { Chat, ChartLine, Dashboard, DataBase, Renew } from "@carbon/icons-react";
import { Button, CodeSnippet, Tag } from "@carbon/react";
import { useEffect, useState } from "react";
import type { CatalystApi } from "./api";
import { catalystApi } from "./api";
import { ExecutionState } from "./components/ExecutionState";
import { DatasetBrowser } from "./components/DatasetBrowser";
import { DashboardPublishPanel } from "./components/DashboardPublishPanel";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { QueryPreview } from "./components/QueryPreview";
import { QuestionForm } from "./components/QuestionForm";
import { ResultsTable } from "./components/ResultsTable";
import {
  TurnNotebook,
  type NotebookGrounding,
  type NotebookTurn,
} from "./components/TurnNotebook";
import { WorkbenchPanel } from "./components/WorkbenchPanel";
import {
  editorContentMatchesVersion,
  workbenchEditorDigest,
} from "./editorDigest";
import {
  isPreview,
  isTable,
  type BoundParameter,
  type CatalystExecutionOutcome,
  type CatalystPolicyOutcome,
  type CatalystPreview,
  type CatalystQueryOutcome,
  type DataSourcesResponse,
  type DashboardBuilderSection,
  type CatalystTable,
  type QueryOptions,
  type WorkbenchEditorCatalog,
  type WorkbenchGenerationEvidence,
  type WorkbenchQueryVersion,
  type WorkbenchSession,
  type WorkbenchTurnRequest,
  type WorkbenchTurnTimeline,
} from "./types";

type WorkflowState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "preview"; preview: CatalystPreview; executing: boolean }
  | { kind: "query-outcome"; outcome: CatalystQueryOutcome }
  | { kind: "policy-outcome"; outcome: CatalystPolicyOutcome }
  | {
      kind: "polling";
      preview: CatalystPreview;
      idempotencyKey: string;
      outcome: CatalystExecutionOutcome;
    }
  | { kind: "execution-outcome"; outcome: CatalystExecutionOutcome }
  | { kind: "result"; result: CatalystTable }
  | { kind: "error"; message: string };

interface QueryWorkspaceProps {
  api?: CatalystApi;
  pollIntervalMs?: number;
}

const dashboardSections: Array<{
  id: DashboardBuilderSection;
  label: string;
  icon: typeof Chat;
}> = [
  { id: "ask", label: "Ask", icon: Chat },
  { id: "datasets", label: "Datasets", icon: DataBase },
  { id: "widgets", label: "Widgets", icon: ChartLine },
  { id: "dashboards", label: "Dashboards", icon: Dashboard },
];

const messageFromError = (error: unknown) =>
  error instanceof Error ? error.message : "An unexpected request error occurred.";

const ACTIVE_WORKBENCH_SESSION_KEY = "catalyst.workbench.activeSessionId";

const readActiveWorkbenchSessionId = () => {
  try {
    return globalThis.localStorage?.getItem(ACTIVE_WORKBENCH_SESSION_KEY) ?? null;
  } catch {
    return null;
  }
};

const rememberActiveWorkbenchSession = (sessionId: string) => {
  try {
    globalThis.localStorage?.setItem(ACTIVE_WORKBENCH_SESSION_KEY, sessionId);
  } catch {
    // Server persistence still works when browser storage is unavailable.
  }
};

const forgetActiveWorkbenchSession = () => {
  try {
    globalThis.localStorage?.removeItem(ACTIVE_WORKBENCH_SESSION_KEY);
  } catch {
    // Nothing else is required when browser storage is unavailable.
  }
};

// The selected data source lives in the URL so a reload, a bookmark, or a
// pasted link all reopen the same dataset, and so support can tell which
// source a reported screen was actually reading.
const DATA_SOURCE_QUERY_KEY = "dataSource";

const readDataSourceIdFromUrl = () => {
  try {
    const search = globalThis.location?.search;
    if (!search) return "";
    return new URLSearchParams(search).get(DATA_SOURCE_QUERY_KEY) ?? "";
  } catch {
    return "";
  }
};

const writeDataSourceIdToUrl = (dataSourceId: string) => {
  try {
    const { location, history } = globalThis;
    if (!location || !history?.replaceState) return;
    const url = new URL(location.href);
    if (url.searchParams.get(DATA_SOURCE_QUERY_KEY) === (dataSourceId || null)) {
      return;
    }
    if (dataSourceId) {
      url.searchParams.set(DATA_SOURCE_QUERY_KEY, dataSourceId);
    } else {
      url.searchParams.delete(DATA_SOURCE_QUERY_KEY);
    }
    // replaceState, not pushState: switching sources reframes the current view
    // rather than adding a step the back button should walk through.
    history.replaceState(history.state, "", url);
  } catch {
    // A non-browser host (tests, SSR) still works from component state alone.
  }
};

const sessionEditorDraft = (session: WorkbenchSession) =>
  session.currentVersion ?? session.draftSeed ?? null;

const editorExpectedColumns = (
  baseVersion: WorkbenchQueryVersion | null,
  sql: string,
) =>
  baseVersion !== null && sql === baseVersion.sql
    ? baseVersion.expectedColumns
    : [];

const currentQueryProfileId = (session: WorkbenchSession) => {
  const visited = new Set<string>();
  let version = session.currentVersion;
  while (version && !visited.has(version.versionId)) {
    visited.add(version.versionId);
    const direct = version.provenance.profileId;
    if (typeof direct === "string" && direct) return direct;
    const nested = version.provenance.profileSnapshot;
    const nestedProfileId =
      typeof nested === "object" && nested !== null && !Array.isArray(nested)
        ? (nested as Record<string, unknown>).profileId
        : undefined;
    if (
      typeof nestedProfileId === "string" &&
      nestedProfileId
    ) {
      return nestedProfileId;
    }
    const parentVersionId = version.parentVersionId;
    version = parentVersionId
      ? session.versions.find((candidate) => candidate.versionId === parentVersionId) ?? null
      : null;
  }
  return session.profileId;
};

const notebookTurns = (
  timeline: WorkbenchTurnTimeline | null,
  session: WorkbenchSession | null,
  sources?: DataSourcesResponse | null,
): NotebookTurn[] =>
  (timeline?.turns ?? []).map((turn) => ({
    turnId: turn.turnId,
    ordinal: turn.ordinal,
    kind: turn.kind,
    instruction: turn.instruction,
    dataSourceLabel: turn.dataSourceId
      ? (sources?.dataSources.find((s) => s.id === turn.dataSourceId)?.label ??
        turn.dataSourceId)
      : null,
    status: turn.status,
    selectedVersionId: turn.selectedVersionId,
    profileSnapshot: {
      profileName: turn.profileSnapshot.profileName,
      writer: turn.profileSnapshot.writer
        ? { modelId: turn.profileSnapshot.writer.modelId }
        : null,
      reviewer: turn.profileSnapshot.reviewer
        ? { modelId: turn.profileSnapshot.reviewer.modelId }
        : null,
    },
    outputVersions: turn.outputVersions.map((output) => ({
      selected: output.selected,
      role: output.role,
      contractValid: output.contractValid,
      versionId: output.versionId,
      version: session?.versions.find(
        (version) => version.versionId === output.versionId,
      ),
    })),
    failure: turn.failure ? { message: turn.failure.message } : null,
  }));

const notebookGrounding = (
  session: WorkbenchSession,
  sql: string,
  parameters: BoundParameter[],
): NotebookGrounding => {
  const baseVersion = session.currentVersion;
  const content = {
    sql,
    parameters,
    expectedColumns: editorExpectedColumns(baseVersion, sql),
  };
  const editorDigest = editorContentMatchesVersion(content, baseVersion)
    ? baseVersion!.queryDigest
    : workbenchEditorDigest(content);
  const execution = [...session.executions]
    .sort((left, right) => right.ordinal - left.ordinal)
    .find((candidate) => candidate.queryDigest === editorDigest);

  if (execution) {
    const version = session.versions.find(
      (candidate) => candidate.versionId === execution.versionId,
    );
    const queryLabel = version ? `Query v${version.ordinal}` : "the current query";
    if (execution.status === "failed") {
      return {
        kind: "matching",
        text:
          `Execution summary: ${queryLabel} · Run ${execution.ordinal} failed. ` +
          "The database diagnostic is available to the model; result row values are not.",
      };
    }
    const returned = execution.result?.rowCount.returned;
    return {
      kind: "matching",
      text:
        `Execution summary: ${queryLabel} · Run ${execution.ordinal}` +
        (returned === undefined
          ? ". "
          : ` · ${returned} ${returned === 1 ? "row" : "rows"}. `) +
        "Result row values are not included in model context.",
    };
  }

  if (session.executions.length > 0) {
    return {
      kind: "stale",
      text:
        "Displayed results are stale for this editor. Run the current SQL to include " +
        "a matching execution summary; result row values are not included.",
    };
  }

  return {
    kind: "not-executed",
    text:
      "This query has not been executed. Refinement uses the current SQL without " +
      "an execution summary or result row values.",
  };
};

const createIdempotencyKey = () => {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `catalyst-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const executionHeading = (outcome: CatalystExecutionOutcome) =>
  ({
    in_progress: "Query running",
    not_found: "Execution not found",
    conflict: "Execution conflict",
    failed: "Execution failed",
  })[outcome.status];

const executionKind = (
  outcome: CatalystExecutionOutcome,
): "info" | "warning" | "error" => {
  if (outcome.status === "in_progress") return "info";
  if (outcome.status === "conflict") {
    return "warning";
  }
  return "error";
};

const QueryOutcomeState = ({ outcome }: { outcome: CatalystQueryOutcome }) => {
  const content = {
    needs_clarification: {
      title: "Clarification needed",
      message: outcome.clarification ?? "Please clarify the question.",
      kind: "info" as const,
    },
    unsupported: {
      title: "Question unsupported",
      message: outcome.message ?? "This question is not supported.",
      kind: "warning" as const,
    },
    rejected: {
      title: "Question rejected",
      message: outcome.message ?? "The query request was rejected.",
      kind: "error" as const,
    },
  }[outcome.status];
  const diagnostic = outcome.diagnosticCandidate;
  const candidate = diagnostic?.candidate;

  return (
    <ExecutionState
      title={content.title}
      message={content.message}
      kind={content.kind}
      details={
        <div className="outcome-details">
          {diagnostic && (
            <section
              className="diagnostic-candidate"
              aria-labelledby="diagnostic-candidate-title"
            >
              <div className="diagnostic-candidate__heading">
                <h3 id="diagnostic-candidate-title">
                  Generated candidate
                </h3>
                <Tag type="red">Not executable</Tag>
              </div>
              <p className="muted">
                This is the model output retained for diagnosis. It did not pass
                validation and cannot be accepted or run.
              </p>
              {candidate?.sql && (
                <div className="preview-block">
                  <h3>SQL</h3>
                  <div aria-label="Rejected generated SQL">
                    <CodeSnippet type="multi" feedback="Copied">
                      {candidate.sql}
                    </CodeSnippet>
                  </div>
                </div>
              )}
              {candidate?.parameters && (
                <div className="preview-block">
                  <h3>Typed parameters</h3>
                  {candidate.parameters.length === 0 ? (
                    <p className="muted">No bound parameters.</p>
                  ) : (
                    <dl className="diagnostic-parameters">
                      {candidate.parameters.map((parameter) => (
                        <div key={parameter.name}>
                          <dt>{parameter.name}</dt>
                          <dd>
                            <Tag size="sm" type="cool-gray">
                              {parameter.type}
                            </Tag>{" "}
                            {Array.isArray(parameter.value)
                              ? JSON.stringify(parameter.value)
                              : String(parameter.value)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              )}
              {diagnostic.rawOutput && (
                <div className="preview-block">
                  <h3>Raw model output</h3>
                  <div aria-label="Rejected raw model output">
                    <CodeSnippet type="multi" feedback="Copied">
                      {diagnostic.rawOutput}
                    </CodeSnippet>
                  </div>
                </div>
              )}
              {diagnostic.attempts && diagnostic.attempts.length > 0 && (
                <div className="preview-block">
                  <h3>Validation feedback</h3>
                  <ol className="diagnostic-attempts">
                    {diagnostic.attempts.map((attempt) => (
                      <li key={attempt.attempt}>
                        <strong>Attempt {attempt.attempt}</strong>
                        {attempt.findings.length === 0 ? (
                          <p>No deterministic findings.</p>
                        ) : (
                          <ul>
                            {attempt.findings.map((finding) => (
                              <li key={`${attempt.attempt}-${finding.code}`}>
                                <code>{finding.code}</code> — {finding.message}
                                {finding.suggestedAction && (
                                  <p>Suggested update: {finding.suggestedAction}</p>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </section>
          )}
          <p className="trace-line">
            Hub trace: <span>{outcome.provenance.traceId}</span>
          </p>
        </div>
      }
    />
  );
};

export const QueryWorkspace = ({
  api = catalystApi,
  pollIntervalMs = 1000,
}: QueryWorkspaceProps) => {
  const [activeSection, setActiveSection] = useState<DashboardBuilderSection>("ask");
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<WorkflowState>({ kind: "idle" });
  const [queryOptions, setQueryOptions] = useState<QueryOptions | null>(null);
  const [profileId, setProfileId] = useState("");
  const [dataSources, setDataSources] = useState<DataSourcesResponse | null>(
    null,
  );
  const [dataSourceId, setDataSourceId] = useState(readDataSourceIdFromUrl);
  const [workbenchSession, setWorkbenchSession] =
    useState<WorkbenchSession | null>(null);
  const [workbenchSql, setWorkbenchSql] = useState("");
  const [workbenchParameters, setWorkbenchParameters] = useState<
    BoundParameter[]
  >([]);
  const [workbenchCatalog, setWorkbenchCatalog] =
    useState<WorkbenchEditorCatalog | null>(null);
  const [workbenchCatalogFailed, setWorkbenchCatalogFailed] = useState(false);
  const [workbenchWrapLines, setWorkbenchWrapLines] = useState(true);
  const [workbenchBusy, setWorkbenchBusy] = useState<
    "validating" | "running" | null
  >(null);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);
  const [workbenchAnnouncement, setWorkbenchAnnouncement] = useState("");
  const [sqlEditorFocusRequestId, setSqlEditorFocusRequestId] = useState(0);
  const [workbenchTimeline, setWorkbenchTimeline] =
    useState<WorkbenchTurnTimeline | null>(null);
  const [followupInstruction, setFollowupInstruction] = useState("");
  const [followupBusy, setFollowupBusy] = useState(false);
  const [generationEvidence, setGenerationEvidence] =
    useState<WorkbenchGenerationEvidence | null>(null);
  const [generationEvidenceLoadingTurnId, setGenerationEvidenceLoadingTurnId] =
    useState<string | null>(null);
  const [generationEvidenceError, setGenerationEvidenceError] =
    useState<string | null>(null);
  const usesWorkbench = Boolean(
    api.createWorkbenchSession &&
      api.createWorkbenchVersion &&
      api.executeWorkbenchVersion,
  );
  const usesNotebook = Boolean(
    api.createWorkbenchTurn && api.getWorkbenchTurns,
  );
  const availableProfiles = queryOptions?.profiles.filter(
    (profile) => profile.available,
  ) ?? [];
  const fallbackProfileId =
    availableProfiles.find(
      (profile) => profile.id === queryOptions?.defaultProfileId,
    )?.id ?? availableProfiles[0]?.id ?? "";
  const selectedAvailableProfileId = availableProfiles.some(
    (profile) => profile.id === profileId,
  )
    ? profileId
    : fallbackProfileId;
  const revisionProfiles = queryOptions?.profiles.filter(
    (profile) => profile.available && profile.revisionCapable === true,
  ) ?? [];
  const noAvailableProfiles =
    queryOptions !== null &&
    !queryOptions.profiles.some((profile) => profile.available);
  const fallbackRevisionProfileId =
    revisionProfiles.find(
      (profile) => profile.id === queryOptions?.defaultProfileId,
    )?.id ?? revisionProfiles[0]?.id ?? "";
  const selectedRevisionProfileId = revisionProfiles.some(
    (profile) => profile.id === profileId,
  )
    ? profileId
    : fallbackRevisionProfileId;

  useEffect(() => {
    if (!api.getQueryOptions) return;
    const controller = new AbortController();
    api.getQueryOptions(controller.signal)
      .then((options) => {
        setQueryOptions(options);
        const defaultProfile = options.profiles.find(
          (profile) => profile.id === options.defaultProfileId && profile.available,
        );
        const fallbackProfileId =
          defaultProfile?.id ??
          options.profiles.find((profile) => profile.available)?.id ??
          "";
        setProfileId((currentProfileId) => currentProfileId || fallbackProfileId);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (!api.getDataSources) return;
    const controller = new AbortController();
    api.getDataSources(controller.signal)
      .then((response) => {
        setDataSources(response);
        setDataSourceId((current) => {
          // A URL can name a source this deployment does not register (stale
          // link, renamed source). Fall back to the default rather than
          // sending an id every request would reject.
          const known = response.dataSources.some(
            (source) => source.id === current && source.available,
          );
          return known ? current : response.defaultDataSourceId;
        });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    writeDataSourceIdToUrl(dataSourceId);
  }, [dataSourceId]);

  useEffect(() => {
    if (!api.getWorkbenchCatalog) return;
    const controller = new AbortController();
    api.getWorkbenchCatalog(dataSourceId || undefined, controller.signal)
      .then((catalog) => {
        setWorkbenchCatalog(catalog);
        setWorkbenchCatalogFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setWorkbenchCatalog(null);
          setWorkbenchCatalogFailed(true);
        }
      });
    return () => controller.abort();
  }, [api, dataSourceId]);

  useEffect(() => {
    if (!api.getWorkbenchSession) return;
    const sessionId = readActiveWorkbenchSessionId();
    if (!sessionId) return;
    const controller = new AbortController();
    api.getWorkbenchSession(sessionId, controller.signal)
      .then((session) => {
        setWorkbenchSession(session);
        setQuestion(session.question);
        setProfileId(currentQueryProfileId(session));
        if (session.dataSourceId) {
          setDataSourceId(session.dataSourceId);
        }
        const draft = sessionEditorDraft(session);
        setWorkbenchSql(draft?.sql ?? "");
        setWorkbenchParameters(
          draft?.parameters.map((parameter) => ({ ...parameter })) ?? [],
        );
        setWorkbenchWrapLines(
          typeof session.browserState.sqlWrapLines === "boolean"
            ? session.browserState.sqlWrapLines
            : true,
        );
        if (api.getWorkbenchTurns) {
          void api.getWorkbenchTurns(sessionId, controller.signal)
            .then((timeline) => {
              if (!controller.signal.aborted) setWorkbenchTimeline(timeline);
            })
            .catch(() => {
              if (!controller.signal.aborted) setWorkbenchTimeline(null);
            });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) forgetActiveWorkbenchSession();
      });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (state.kind !== "polling") return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await api.pollExecution(
          state.preview.previewId,
          state.idempotencyKey,
          controller.signal,
        );
        if (isTable(response)) {
          setState({ kind: "result", result: response });
        } else if (response.status === "in_progress") {
          setState({ ...state, outcome: response });
        } else {
          setState({ kind: "execution-outcome", outcome: response });
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setState({ kind: "error", message: messageFromError(error) });
        }
      }
    }, pollIntervalMs);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, pollIntervalMs, state]);

  const submitQuestion = async (normalizedQuestion: string) => {
    setState({ kind: "submitting" });
    setWorkbenchError(null);
    try {
      if (usesWorkbench) {
        const session = await api.createWorkbenchSession!(
          normalizedQuestion,
          (queryOptions && selectedAvailableProfileId) || undefined,
          undefined,
          dataSourceId || undefined,
        );
        setWorkbenchSession(session);
        rememberActiveWorkbenchSession(session.sessionId);
        const draft = sessionEditorDraft(session);
        setWorkbenchSql(draft?.sql ?? "");
        setWorkbenchParameters(
          draft?.parameters.map((parameter) => ({ ...parameter })) ?? [],
        );
        setWorkbenchWrapLines(
          typeof session.browserState.sqlWrapLines === "boolean"
            ? session.browserState.sqlWrapLines
            : true,
        );
        if (api.getWorkbenchTurns) {
          setWorkbenchTimeline(await api.getWorkbenchTurns(session.sessionId));
        } else {
          setWorkbenchTimeline(null);
        }
        setState({ kind: "idle" });
        return;
      }
      const response = queryOptions && profileId
        ? await api.submitQuestion(normalizedQuestion, profileId)
        : await api.submitQuestion(normalizedQuestion);
      if (isPreview(response)) {
        setState({ kind: "preview", preview: response, executing: false });
      } else if (response.contractVersion === "catalyst.query.v1") {
        setState({ kind: "query-outcome", outcome: response });
      } else {
        setState({ kind: "policy-outcome", outcome: response });
      }
    } catch (error) {
      setState({ kind: "error", message: messageFromError(error) });
    }
  };

  const acceptPreview = async () => {
    if (state.kind !== "preview" || state.executing) return;
    const preview = state.preview;
    const idempotencyKey = createIdempotencyKey();
    setState({ kind: "preview", preview, executing: true });
    try {
      const response = await api.executePreview(preview, idempotencyKey);
      if (isTable(response)) {
        setState({ kind: "result", result: response });
      } else if (response.status === "in_progress") {
        setState({
          kind: "polling",
          preview,
          idempotencyKey,
          outcome: response,
        });
      } else {
        setState({ kind: "execution-outcome", outcome: response });
      }
    } catch (error) {
      setState({ kind: "error", message: messageFromError(error) });
    }
  };

  const startNewSession = () => {
    if (followupBusy) return;
    if (selectedAvailableProfileId) {
      setProfileId(selectedAvailableProfileId);
    }
    setQuestion("");
    setState({ kind: "idle" });
    setWorkbenchSession(null);
    setWorkbenchSql("");
    setWorkbenchParameters([]);
    setWorkbenchBusy(null);
    setWorkbenchError(null);
    setWorkbenchAnnouncement("");
    setSqlEditorFocusRequestId(0);
    setWorkbenchTimeline(null);
    setFollowupInstruction("");
    setFollowupBusy(false);
    setGenerationEvidence(null);
    setGenerationEvidenceLoadingTurnId(null);
    setGenerationEvidenceError(null);
    forgetActiveWorkbenchSession();
    setActiveSection("ask");
    window.setTimeout(() => {
      document.getElementById("catalyst-question")?.focus();
    }, 0);
  };

  const clearWorkbenchDraft = () => {
    if (followupBusy) return;
    setWorkbenchSql("");
    setWorkbenchParameters([]);
    setWorkbenchError(null);
  };

  const restoreCurrentWorkbenchVersion = () => {
    const current = workbenchSession?.currentVersion;
    if (!current) return;
    setWorkbenchSql(current.sql);
    setWorkbenchParameters(
      current.parameters.map((parameter) => ({ ...parameter })),
    );
    setWorkbenchError(null);
  };

  const persistWorkbenchDraft = async (): Promise<{
    session: WorkbenchSession;
    version: WorkbenchQueryVersion;
  }> => {
    if (!workbenchSession || !api.createWorkbenchVersion) {
      throw new Error("The manual workbench is not available.");
    }
    const parent = workbenchSession.currentVersion;
    const session = await api.createWorkbenchVersion(workbenchSession.sessionId, {
      ...(parent
        ? {
            parentVersionId: parent.versionId,
            parentQueryDigest: parent.queryDigest,
          }
        : {}),
      sql: workbenchSql,
      parameters: workbenchParameters,
      expectedColumns: editorExpectedColumns(parent, workbenchSql),
      ...(dataSourceId ? { dataSourceId } : {}),
    });
    if (!session.currentVersion) {
      throw new Error("Catalyst did not return the saved query version.");
    }
    setWorkbenchSession(session);
    setWorkbenchTimeline((current) =>
      current?.sessionId === session.sessionId
        ? {
            ...current,
            currentVersion: {
              versionId: session.currentVersion!.versionId,
              queryDigest: session.currentVersion!.queryDigest,
            },
          }
        : current,
    );
    return { session, version: session.currentVersion };
  };

  const validateWorkbenchDraft = async () => {
    if (workbenchBusy || followupBusy) return;
    setWorkbenchBusy("validating");
    setWorkbenchError(null);
    try {
      await persistWorkbenchDraft();
    } catch (error) {
      setWorkbenchError(messageFromError(error));
    } finally {
      setWorkbenchBusy(null);
    }
  };

  const runWorkbenchDraft = async () => {
    if (workbenchBusy || followupBusy || !api.executeWorkbenchVersion) return;
    setWorkbenchBusy("running");
    setWorkbenchError(null);
    try {
      const { session, version } = await persistWorkbenchDraft();
      const execution = await api.executeWorkbenchVersion(
        version.versionId,
        version.queryDigest,
        createIdempotencyKey(),
      );
      setWorkbenchSession({
        ...session,
        executions: [...session.executions, execution],
      });
    } catch (error) {
      setWorkbenchError(messageFromError(error));
    } finally {
      setWorkbenchBusy(null);
    }
  };

  const generateNextWorkbenchQuery = async () => {
    if (
      followupBusy ||
      workbenchBusy ||
      !workbenchSession ||
      !api.createWorkbenchTurn ||
      !selectedRevisionProfileId ||
      !workbenchSql.trim()
    ) return;
    if (!followupInstruction.trim()) {
      setWorkbenchError("Enter a follow-up instruction for the current query.");
      document.getElementById("catalyst-followup")?.focus();
      return;
    }

    setFollowupBusy(true);
    setWorkbenchError(null);
    setWorkbenchAnnouncement("");
    setGenerationEvidence(null);
    setGenerationEvidenceError(null);
    const baseVersion = workbenchSession.currentVersion;
    const content = {
      sql: workbenchSql,
      parameters: workbenchParameters,
      expectedColumns: editorExpectedColumns(baseVersion, workbenchSql),
    };
    const editorDigest = editorContentMatchesVersion(content, baseVersion)
      ? baseVersion!.queryDigest
      : workbenchEditorDigest(content);
    const request: WorkbenchTurnRequest = {
      contractVersion: "catalyst.workbench.turn.request.v1",
      instruction: followupInstruction,
      profileId: selectedRevisionProfileId,
      ...(dataSourceId ? { dataSourceId } : {}),
      observedBase: baseVersion
        ? {
            versionId: baseVersion.versionId,
            queryDigest: baseVersion.queryDigest,
          }
        : null,
      editorSnapshot: {
        contractVersion: "catalyst.workbench.editor-snapshot.v1",
        ...content,
        editorDigest,
      },
    };

    try {
      const turn = await api.createWorkbenchTurn(
        workbenchSession.sessionId,
        request,
      );
      setWorkbenchTimeline((current) => {
        if (!current || current.sessionId !== workbenchSession.sessionId) {
          return current;
        }
        const turns = current.turns.some((item) => item.turnId === turn.turnId)
          ? current.turns.map((item) => item.turnId === turn.turnId ? turn : item)
          : [...current.turns, turn];
        return {
          ...current,
          currentTurnId: turn.turnId,
          currentVersion: turn.resultingCurrentVersion,
          turns,
        };
      });

      const restored = api.getWorkbenchSession
        ? await api.getWorkbenchSession(workbenchSession.sessionId)
        : workbenchSession;
      setWorkbenchSession(restored);
      const draft = sessionEditorDraft(restored);
      setWorkbenchSql(draft?.sql ?? workbenchSql);
      setWorkbenchParameters(
        draft?.parameters.map((parameter) => ({ ...parameter })) ??
          workbenchParameters,
      );
      if (api.getWorkbenchTurns) {
        setWorkbenchTimeline(
          await api.getWorkbenchTurns(workbenchSession.sessionId),
        );
      }
      if (
        turn.status === "completed" &&
        turn.resultingCurrentVersion &&
        restored.currentVersion?.versionId ===
          turn.resultingCurrentVersion.versionId
      ) {
        setWorkbenchAnnouncement(
          `Query v${restored.currentVersion.ordinal} generated. ` +
            "The SQL editor now contains the successor query.",
        );
        setSqlEditorFocusRequestId((requestId) => requestId + 1);
      }
      setFollowupInstruction("");
    } catch (error) {
      setWorkbenchError(messageFromError(error));
    } finally {
      setFollowupBusy(false);
    }
  };

  const showWorkbenchGenerationEvidence = async (turnId: string) => {
    if (!workbenchSession || !api.getWorkbenchGenerationEvidence) {
      setGenerationEvidenceError("Generation evidence is unavailable.");
      return;
    }
    setGenerationEvidenceLoadingTurnId(turnId);
    setGenerationEvidenceError(null);
    try {
      setGenerationEvidence(
        await api.getWorkbenchGenerationEvidence(
          workbenchSession.sessionId,
          turnId,
        ),
      );
    } catch (error) {
      setGenerationEvidenceError(messageFromError(error));
    } finally {
      setGenerationEvidenceLoadingTurnId(null);
    }
  };

  const updateWorkbenchWrapLines = (wrapLines: boolean) => {
    setWorkbenchWrapLines(wrapLines);
    if (!workbenchSession || !api.updateWorkbenchBrowserState) return;
    const sessionId = workbenchSession.sessionId;
    const browserState = {
      ...workbenchSession.browserState,
      sqlWrapLines: wrapLines,
    };
    void api.updateWorkbenchBrowserState(sessionId, browserState)
      .then((restored) => {
        setWorkbenchSession((current) =>
          current?.sessionId === sessionId
            ? { ...current, browserState: restored.browserState }
            : current,
        );
      })
      .catch(() => undefined);
  };

  const questionIsLocked =
    state.kind === "preview" ||
    state.kind === "polling" ||
    workbenchSession !== null;

  const activeNotebookTurns = notebookTurns(
    workbenchTimeline,
    workbenchSession,
    dataSources,
  );

  const activeGrounding = workbenchSession
    ? notebookGrounding(workbenchSession, workbenchSql, workbenchParameters)
    : null;
  const hasRefineDock = Boolean(
    usesNotebook && workbenchSession && workbenchTimeline,
  );
  const hasQueryDock = hasRefineDock || workbenchSession === null;

  return (
    <div className="dashboard-builder-shell">
      <nav className="dashboard-navigation" aria-label="Catalyst">
        <div className="dashboard-navigation__brand">
          <span aria-hidden="true">C</span>
          <div>
            <strong>Catalyst</strong>
            <small>Dashboard builder</small>
          </div>
        </div>
        <div className="dashboard-navigation__items">
          {dashboardSections.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className="dashboard-navigation__item"
              aria-current={activeSection === id ? "page" : undefined}
              onClick={() => setActiveSection(id)}
            >
              <Icon size={20} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </div>
        <div className="dashboard-navigation__source">
          {dataSources && dataSources.dataSources.some((source) => source.available) ? (
            <label htmlFor="catalyst-data-source">
              <span>Data source</span>
              <select
                id="catalyst-data-source"
                value={dataSourceId}
                disabled={followupBusy || state.kind === "submitting"}
                onChange={(event) => setDataSourceId(event.currentTarget.value)}
              >
                {dataSources.dataSources
                  .filter((source) => source.available)
                  .map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.label}
                    </option>
                  ))}
              </select>
            </label>
          ) : (
            <span>OpenELIS</span>
          )}
        </div>
      </nav>

      <main
        className={`app-shell${hasQueryDock && activeSection === "ask" ? " app-shell--with-query-dock" : ""}`}
      >
        <section hidden={activeSection !== "ask"} aria-labelledby="question-title">
          <header className="dashboard-page-header">
            <div>
              <p className="eyebrow">Ask OpenELIS</p>
              <h1 id="question-title" tabIndex={-1}>
                {workbenchSession ? workbenchSession.question : "Ask OpenELIS"}
              </h1>
              <p>
                Nothing is saved until you review it. Drafts stay in this thread.
              </p>
            </div>
            {workbenchSession && (
              <Button
                type="button"
                kind="tertiary"
                size="sm"
                disabled={followupBusy || workbenchBusy !== null}
                onClick={startNewSession}
              >
                New session
              </Button>
            )}
          </header>

          {workbenchSession && (
            <div className="dashboard-session-meta">
              Session {workbenchSession.sessionId.slice(0, 8)}
              {workbenchTimeline
                ? ` · ${workbenchTimeline.turns.length} turn${
                    workbenchTimeline.turns.length === 1 ? "" : "s"
                  }`
                : ""}
            </div>
          )}

          <DatasetBrowser
            api={api}
            catalog={workbenchCatalog}
            catalogLoadingFailed={workbenchCatalogFailed}
            dataSourceId={dataSourceId || undefined}
            compact
          />

      {!workbenchSession && (
        <QuestionForm
          question={question}
          busy={state.kind === "submitting"}
          disabled={questionIsLocked || noAvailableProfiles}
          onQuestionChange={setQuestion}
          onSubmit={submitQuestion}
          profiles={queryOptions?.profiles ?? []}
          selectedProfileId={selectedAvailableProfileId}
          onProfileChange={setProfileId}
        />
      )}

      {state.kind === "submitting" && (
        <ExecutionState
          title={
            usesWorkbench ? "Generating workbench draft" : "Preparing preview"
          }
          message={
            usesWorkbench
              ? "Catalyst is generating an editable SQL draft with the selected profile."
              : "Catalyst is validating the question and proposed query."
          }
          running
          loadingDescription="Generating answer"
        />
      )}

      {usesNotebook && workbenchSession && workbenchTimeline && (
        <TurnNotebook
          turns={activeNotebookTurns}
          baseVersion={workbenchSession.currentVersion}
          instruction={followupInstruction}
          profiles={queryOptions?.profiles ?? []}
          selectedProfileId={selectedRevisionProfileId}
          grounding={activeGrounding!}
          editorEmpty={!workbenchSql.trim()}
          editorState={
            workbenchSession.currentVersion
              ? "ready"
              : workbenchSession.draftSeed
                ? "unresolved"
                : "empty"
          }
          busy={followupBusy || workbenchBusy !== null}
          generating={followupBusy}
          evidence={generationEvidence}
          evidenceLoadingTurnId={generationEvidenceLoadingTurnId}
          evidenceError={generationEvidenceError}
          onInstructionChange={setFollowupInstruction}
          onProfileChange={setProfileId}
          onGenerate={generateNextWorkbenchQuery}
          onShowEvidence={showWorkbenchGenerationEvidence}
        />
      )}

      {workbenchSession && (
        <WorkbenchPanel
          session={workbenchSession}
          sql={workbenchSql}
          parameters={workbenchParameters}
          editorCatalog={workbenchCatalog}
          catalogLoadingFailed={workbenchCatalogFailed}
          wrapLines={workbenchWrapLines}
          busy={followupBusy ? "generating" : workbenchBusy}
          error={workbenchError}
          announcement={workbenchAnnouncement}
          sqlEditorFocusRequestId={sqlEditorFocusRequestId}
          showExecutionResult={workbenchSession.executions.some(
            (execution) =>
              execution.status === "failed" &&
              execution.ordinal === Math.max(
                ...workbenchSession.executions.map((candidate) => candidate.ordinal),
              ),
          )}
          showInitialGenerationEvidence={!usesNotebook}
          onSqlChange={setWorkbenchSql}
          onParametersChange={setWorkbenchParameters}
          onWrapLinesChange={updateWorkbenchWrapLines}
          onClearDraft={clearWorkbenchDraft}
          onRestoreCurrentVersion={restoreCurrentWorkbenchVersion}
          onValidate={validateWorkbenchDraft}
          onRun={runWorkbenchDraft}
        />
      )}

      {state.kind === "preview" && (
        <QueryPreview
          preview={state.preview}
          executing={state.executing}
          onAccept={acceptPreview}
        />
      )}

      {state.kind === "query-outcome" && (
        <QueryOutcomeState outcome={state.outcome} />
      )}

      {state.kind === "policy-outcome" && (
        <ExecutionState
          title="Catalyst policy rejection"
          message={state.outcome.message}
          kind="error"
          details={
            <div className="outcome-details">
              <ul>
                {state.outcome.violations.map((violation) => (
                  <li key={`${violation.code}-${violation.message}`}>
                    {violation.message}
                  </li>
                ))}
              </ul>
              <p>Trace: {state.outcome.catalystTraceId}</p>
            </div>
          }
        />
      )}

      {state.kind === "polling" && (
        <ExecutionState
          title="Query running"
          message={state.outcome.message}
          kind="info"
          running
          loadingDescription="Running query"
        />
      )}

      {state.kind === "execution-outcome" && (
        <ExecutionState
          title={executionHeading(state.outcome)}
          message={state.outcome.message}
          kind={executionKind(state.outcome)}
          actionLabel="Start a new query"
          onAction={startNewSession}
        />
      )}

      {state.kind === "result" && (
        <>
          <ResultsTable result={state.result} />
          <ProvenancePanel result={state.result} />
          <div className="new-query-action">
            <Button kind="tertiary" renderIcon={Renew} onClick={startNewSession}>
              Start a new query
            </Button>
          </div>
        </>
      )}

      {state.kind === "error" && (
        <ExecutionState
          title="Request failed"
          message={state.message}
          kind="error"
        />
      )}
        </section>

        <DashboardPublishPanel
          api={api}
          session={workbenchSession}
          sql={workbenchSql}
          parameters={workbenchParameters}
          activeSection={activeSection}
          disabled={followupBusy || workbenchBusy !== null}
          onNavigate={setActiveSection}
        />
    </main>
    </div>
  );
};
