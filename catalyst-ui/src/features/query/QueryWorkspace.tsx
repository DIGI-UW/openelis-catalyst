import { Renew } from "@carbon/icons-react";
import { Button, CodeSnippet, Tag } from "@carbon/react";
import { useEffect, useState } from "react";
import type { CatalystApi } from "./api";
import { catalystApi } from "./api";
import { ExecutionState } from "./components/ExecutionState";
import { DatasetBrowser } from "./components/DatasetBrowser";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { QueryPreview } from "./components/QueryPreview";
import { QuestionForm } from "./components/QuestionForm";
import { ResultsTable } from "./components/ResultsTable";
import { WorkbenchPanel } from "./components/WorkbenchPanel";
import {
  isPreview,
  isTable,
  type BoundParameter,
  type CatalystExecutionOutcome,
  type CatalystPolicyOutcome,
  type CatalystPreview,
  type CatalystQueryOutcome,
  type CatalystTable,
  type QueryOptions,
  type WorkbenchEditorCatalog,
  type WorkbenchQueryVersion,
  type WorkbenchSession,
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

const sessionEditorDraft = (session: WorkbenchSession) =>
  session.currentVersion ?? session.draftSeed ?? null;

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
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<WorkflowState>({ kind: "idle" });
  const [queryOptions, setQueryOptions] = useState<QueryOptions | null>(null);
  const [profileId, setProfileId] = useState("");
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
  const usesWorkbench = Boolean(
    api.createWorkbenchSession &&
      api.createWorkbenchVersion &&
      api.executeWorkbenchVersion,
  );

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
    if (!api.getWorkbenchCatalog) return;
    const controller = new AbortController();
    api.getWorkbenchCatalog(controller.signal)
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
  }, [api]);

  useEffect(() => {
    if (!api.getWorkbenchSession) return;
    const sessionId = readActiveWorkbenchSessionId();
    if (!sessionId) return;
    const controller = new AbortController();
    api.getWorkbenchSession(sessionId, controller.signal)
      .then((session) => {
        setWorkbenchSession(session);
        setQuestion(session.question);
        setProfileId(session.profileId);
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
        const session = queryOptions && profileId
          ? await api.createWorkbenchSession!(normalizedQuestion, profileId)
          : await api.createWorkbenchSession!(normalizedQuestion);
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

  const reset = () => {
    setQuestion("");
    setState({ kind: "idle" });
    setWorkbenchSession(null);
    setWorkbenchSql("");
    setWorkbenchParameters([]);
    setWorkbenchBusy(null);
    setWorkbenchError(null);
    forgetActiveWorkbenchSession();
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
      expectedColumns: parent?.expectedColumns ?? [],
    });
    if (!session.currentVersion) {
      throw new Error("Catalyst did not return the saved query version.");
    }
    setWorkbenchSession(session);
    return { session, version: session.currentVersion };
  };

  const validateWorkbenchDraft = async () => {
    if (workbenchBusy) return;
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
    if (workbenchBusy || !api.executeWorkbenchVersion) return;
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
    state.kind === "polling";

  return (
    <main className="app-shell">
      <div className="app-shell__intro">
        <p className="product-mark">OpenELIS Global / Catalyst</p>
        <p>Governed query review and typed table results</p>
      </div>

      <DatasetBrowser api={api} />

      <QuestionForm
        question={question}
        busy={state.kind === "submitting"}
        disabled={questionIsLocked}
        onQuestionChange={setQuestion}
        onSubmit={submitQuestion}
        profiles={queryOptions?.profiles ?? []}
        selectedProfileId={profileId}
        onProfileChange={setProfileId}
      />

      {state.kind === "submitting" && (
        <ExecutionState
          title={
            usesWorkbench ? "Generating workbench draft" : "Preparing preview"
          }
          message={
            usesWorkbench
              ? "Med-Agent Hub is generating an editable SQL draft with the selected profile."
              : "Catalyst is validating the question and proposed query."
          }
          running
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
          busy={workbenchBusy}
          error={workbenchError}
          onSqlChange={setWorkbenchSql}
          onParametersChange={setWorkbenchParameters}
          onWrapLinesChange={updateWorkbenchWrapLines}
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
        />
      )}

      {state.kind === "execution-outcome" && (
        <ExecutionState
          title={executionHeading(state.outcome)}
          message={state.outcome.message}
          kind={executionKind(state.outcome)}
          actionLabel="Start a new query"
          onAction={reset}
        />
      )}

      {state.kind === "result" && (
        <>
          <ResultsTable result={state.result} />
          <ProvenancePanel result={state.result} />
          <div className="new-query-action">
            <Button kind="tertiary" renderIcon={Renew} onClick={reset}>
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
    </main>
  );
};
