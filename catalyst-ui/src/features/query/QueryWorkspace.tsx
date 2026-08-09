import { Renew } from "@carbon/icons-react";
import { Button, CodeSnippet, Tag } from "@carbon/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import type { CatalystApi } from "./api";
import { catalystApi } from "./api";
import { ExecutionState } from "./components/ExecutionState";
import { DatasetBrowser } from "./components/DatasetBrowser";
import { DashboardPublishPanel } from "./components/DashboardPublishPanel";
import { DetailsPanel, type DetailsTab } from "./components/DetailsPanel";
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
import { WorkbenchRail } from "./components/WorkbenchRail";
import {
  clampRailWidth,
  RAIL_DEFAULT_WIDTH,
  RAIL_STACK_BREAKPOINT,
  type RailSection,
  type RailTurn,
} from "./components/workbenchRailSupport";
import {
  editorContentMatchesVersion,
  workbenchEditorDigest,
} from "./editorDigest";
import type { ThemePreference } from "./theme";
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
  type WorkbenchSessionSummary,
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
  themePreference?: ThemePreference;
  onThemePreferenceChange?: (preference: ThemePreference) => void;
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

// The run recorded against a turn's own query version. A version can be run
// more than once (re-run after a timeout), so the newest execution wins.
const executionForVersion = (
  session: WorkbenchSession | null,
  versionId: string | null,
) =>
  versionId === null
    ? null
    : ([...(session?.executions ?? [])]
        .sort((left, right) => right.ordinal - left.ordinal)
        .find((execution) => execution.versionId === versionId) ?? null);

const validationStatusForVersion = (
  session: WorkbenchSession | null,
  versionId: string | null,
) =>
  versionId === null
    ? null
    : ([...(session?.validations ?? [])]
        .sort((left, right) => right.ordinal - left.ordinal)
        .find((validation) => validation.versionId === versionId)?.status ??
      null);

/**
 * Cells for query versions a person wrote and ran by hand.
 *
 * The thread is built from turns, and a turn is a model generation — so a
 * hand-edited version that has been run produces a result with nowhere to go,
 * and the thread appears to stop at the last thing a model wrote. These carry
 * that work into the thread it belongs to.
 */
const manualNotebookTurns = (
  session: WorkbenchSession | null,
  timeline: WorkbenchTurnTimeline | null,
): NotebookTurn[] => {
  if (!session) return [];
  const owned = new Set(
    (timeline?.turns ?? []).flatMap((turn) =>
      turn.outputVersions.map((output) => output.versionId),
    ),
  );
  const seen = new Set<string>();
  return session.versions
    .filter((version) => {
      if (owned.has(version.versionId) || version.authorType !== "human") {
        return false;
      }
      if (seen.has(version.versionId)) return false;
      seen.add(version.versionId);
      return true;
    })
    .sort((left, right) => left.ordinal - right.ordinal)
    .flatMap((version) => {
      const execution = executionForVersion(session, version.versionId);
      // Not yet run: it is still the draft in the editor, not a cell.
      if (!execution) return [];
      return [
        {
          turnId: `manual:${version.versionId}`,
          // Position in the thread is assigned by threadCells, which is the
          // only place that can see both kinds of cell at once.
          ordinal: 0,
          kind: "followup" as const,
          instruction: "Edited by hand",
          status: "completed" as const,
          selectedVersionId: version.versionId,
          outputVersions: [
            {
              selected: true,
              role: "writer" as const,
              contractValid: true,
              versionId: version.versionId,
              version,
            },
          ],
          profileSnapshot: { profileName: null, writer: null, reviewer: null },
          failure: null,
          execution,
          validationStatus: validationStatusForVersion(
            session,
            version.versionId,
          ),
          current: version.versionId === session.currentVersionId,
          createdAt: version.createdAt,
        },
      ];
    });
};

/** A cell with no model generation behind it has no evidence to fetch. */
const isManualCell = (turnId: string) => turnId.startsWith("manual:");

const notebookTurns = (
  timeline: WorkbenchTurnTimeline | null,
  session: WorkbenchSession | null,
): NotebookTurn[] =>
  (timeline?.turns ?? []).map((turn) => ({
    turnId: turn.turnId,
    ordinal: turn.ordinal,
    kind: turn.kind,
    instruction: turn.instruction,
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
    execution: executionForVersion(session, turn.selectedVersionId),
    validationStatus: validationStatusForVersion(session, turn.selectedVersionId),
    current:
      turn.selectedVersionId !== null &&
      turn.selectedVersionId === session?.currentVersionId,
    createdAt: turn.createdAt,
  }));

/**
 * The thread, in the order the work happened.
 *
 * The two kinds of cell are recorded in different places — generations in the
 * turn timeline, hand-edited runs among the session's versions — so appending
 * one list to the other filed every hand edit after model turns that came
 * later. Query versions are numbered in the order they were appended, which is
 * the one clock both kinds share; the cell number is then simply a position in
 * the thread.
 */
const threadCells = (
  timeline: WorkbenchTurnTimeline | null,
  session: WorkbenchSession | null,
): NotebookTurn[] =>
  [
    ...notebookTurns(timeline, session),
    ...manualNotebookTurns(session, timeline),
  ]
    // When it happened, which both kinds of cell record. An earlier version
    // keyed off the selected version's ordinal, which a failed generation
    // does not have -- so it inherited a position just after the turn before
    // it and sorted ahead of every hand edit made since. A turn that produced
    // nothing still happened at a time.
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt))
    .map((turn, index) => ({ ...turn, ordinal: index + 1 }));

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

  // What this says is what the model will be told. Which version and which run
  // produced it is bookkeeping the thread already carries, so it is left out:
  // the only thing that matters here is that the summary matches the editor.
  if (execution) {
    if (execution.status === "failed") {
      return {
        kind: "matching",
        text:
          "Execution summary: this query failed. " +
          "The database diagnostic is available to the model; result row values are not.",
      };
    }
    const returned = execution.result?.rowCount.returned;
    return {
      kind: "matching",
      text:
        "Execution summary: this query ran" +
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

// The composer pins open on a failed run, so the error state is never one
// scroll away from being invisible.
const latestExecutionFailed = (session: WorkbenchSession) =>
  [...session.executions].sort((left, right) => right.ordinal - left.ordinal)[0]
    ?.status === "failed";

const railLayoutFromBrowserState = (
  browserState: Record<string, unknown>,
): { width: number | null; section: RailSection | null } => {
  const width = browserState.railWidth;
  const section = browserState.railSection;
  return {
    width: typeof width === "number" && Number.isFinite(width) ? width : null,
    section: section === "data" || section === "turns" ? section : null,
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
  themePreference = "system",
  onThemePreferenceChange,
}: QueryWorkspaceProps) => {
  const [activeSection, setActiveSection] = useState<DashboardBuilderSection>("ask");
  const [railWidth, setRailWidth] = useState(RAIL_DEFAULT_WIDTH);
  const [railSection, setRailSection] = useState<RailSection>("turns");
  const [activeTurnOrdinal, setActiveTurnOrdinal] = useState<number | null>(null);
  const [sessionMenu, setSessionMenu] = useState<
    "closed" | "list" | "new" | "rename"
  >("closed");
  const [recentSessions, setRecentSessions] = useState<WorkbenchSessionSummary[]>(
    [],
  );
  const [draftSessionName, setDraftSessionName] = useState("");
  const [detailsTurnId, setDetailsTurnId] = useState<string | null>(null);
  // Details can be scoped to the session rather than a turn: a gateway that
  // serves no per-turn evidence still records validation, provenance and
  // versions, and they must stay reachable.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<DetailsTab>("validation");
  const [developerMode, setDeveloperMode] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  // A run asks for the cell that will carry its result; the cell is only in
  // the document a render later, so the request waits here until it exists.
  const revealVersionId = useRef<string | null>(null);
  const revealTurnId = useRef<string | null>(null);
  // The dashboard panel owns the review dialog; the cell that produced the
  // result asks it to open.
  const openDatasetReview = useRef<(() => void) | null>(null);
  const registerDatasetOpener = useCallback(
    (open: (() => void) | null) => {
      openDatasetReview.current = open;
    },
    [],
  );
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? 1440 : window.innerWidth,
  );
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<WorkflowState>({ kind: "idle" });
  const [queryOptions, setQueryOptions] = useState<QueryOptions | null>(null);
  const [profileId, setProfileId] = useState("");
  const [dataSources, setDataSources] = useState<DataSourcesResponse | null>(
    null,
  );
  // What the picker holds before a session exists. Once a session exists its
  // own source wins — see effectiveDataSourceId.
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
  const [workbenchBusy, setWorkbenchBusy] = useState<"running" | null>(null);
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

  // Everything it takes to make a stored session the one on screen. Restore
  // on load and picking one from the rail menu are the same operation.
  // Stable across renders: the restore effect depends on it, and useState
  // setters are already stable, so `api` is its only real dependency.
  const adoptWorkbenchSession = useCallback((session: WorkbenchSession) => {
    setWorkbenchSession(session);
    setQuestion(session.question);
    setProfileId(currentQueryProfileId(session));
    if (session.dataSourceId) setDataSourceId(session.dataSourceId);
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
    const layout = railLayoutFromBrowserState(session.browserState);
    if (layout.width !== null) {
      setRailWidth(clampRailWidth(layout.width, window.innerWidth));
    }
    if (layout.section !== null) setRailSection(layout.section);
    setWorkbenchTimeline(null);
    setDetailsOpen(false);
    setDetailsTurnId(null);
    setWorkbenchError(null);
    if (api.getWorkbenchTurns) {
      void api.getWorkbenchTurns(session.sessionId)
        .then(setWorkbenchTimeline)
        .catch(() => setWorkbenchTimeline(null));
    }
  }, [api]);

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

  // A session is grounded in one catalog, so once one is open its recorded
  // source is authoritative and a `?dataSource=` in the URL cannot retarget it.
  // Reading it from the session rather than mirroring it into state keeps that
  // structural: the Gateway still accepts a turn that targets another source
  // (see tests/test_multi_source.py), so this UI should never send one.
  const effectiveDataSourceId = workbenchSession?.dataSourceId || dataSourceId;

  useEffect(() => {
    writeDataSourceIdToUrl(effectiveDataSourceId);
  }, [effectiveDataSourceId]);

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!api.getWorkbenchCatalog) return;
    const controller = new AbortController();
    api.getWorkbenchCatalog(effectiveDataSourceId || undefined, controller.signal)
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
  }, [api, effectiveDataSourceId]);

  useEffect(() => {
    if (!api.getWorkbenchSession) return;
    const sessionId = readActiveWorkbenchSessionId();
    if (!sessionId) return;
    const controller = new AbortController();
    api.getWorkbenchSession(sessionId, controller.signal)
      .then((session) => {
        adoptWorkbenchSession(session);
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
  }, [api, adoptWorkbenchSession]);

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
      if (
        workbenchSession &&
        !sessionHasWork &&
        api.askWorkbenchSessionQuestion
      ) {
        adoptWorkbenchSession(
          await api.askWorkbenchSessionQuestion(
            workbenchSession.sessionId,
            normalizedQuestion,
            (queryOptions && selectedAvailableProfileId) || undefined,
          ),
        );
        setState({ kind: "idle" });
        return;
      }
      if (usesWorkbench) {
        const session = await api.createWorkbenchSession!(
          normalizedQuestion,
          (queryOptions && selectedAvailableProfileId) || undefined,
          undefined,
          dataSourceId || undefined,
          undefined,
          draftSessionName.trim() || undefined,
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

  // Opening a session is a real act: it is created, named and grounded in a
  // source before any question exists, so the source you are targeting is
  // settled before you decide what to ask.
  const startNewSession = async () => {
    if (followupBusy || !api.createWorkbenchSession) return;
    setSessionMenu("closed");
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
    try {
      const session = await api.createWorkbenchSession(
        "",
        (queryOptions && selectedAvailableProfileId) || undefined,
        undefined,
        dataSourceId || undefined,
        undefined,
        draftSessionName.trim() || undefined,
      );
      rememberActiveWorkbenchSession(session.sessionId);
      adoptWorkbenchSession(session);
      setDraftSessionName("");
      setRailSection("data");
    } catch (error) {
      setWorkbenchError(messageFromError(error));
    }
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
      ...(effectiveDataSourceId ? { dataSourceId: effectiveDataSourceId } : {}),
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
      // Whatever the database said is now the thing to look at — a failure is
      // a result too, and reading its diagnostic is the next step. Editing
      // again is a choice made from there, not the state left behind.
      setEditorOpen(false);
      revealVersionId.current = version.versionId;
    } catch (error) {
      // The action itself failed, so there is no result cell to move to and
      // the editor stays open with the error above it.
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
      ...(effectiveDataSourceId ? { dataSourceId: effectiveDataSourceId } : {}),
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
          "The next query is ready. The SQL editor now contains it.",
        );
        setSqlEditorFocusRequestId((requestId) => requestId + 1);
      }
      if (turn.status === "failed") {
        // A turn that comes back failed is not an error to throw, so it used
        // to be treated as success: nothing was said, the instruction was
        // cleared, and the failed cell was filed wherever the ordering put
        // it. From the composer that is indistinguishable from nothing
        // happening. Say what went wrong, keep what was asked so it can be
        // tried again, and move to the cell that carries the diagnosis.
        setWorkbenchError(
          turn.failure?.message ??
            "The next query could not be generated. The turn records why.",
        );
        revealTurnId.current = turn.turnId;
      } else {
        setFollowupInstruction("");
      }
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

  // Layout the analyst chose — rail width, which rail section is open, whether
  // SQL wraps — is theirs, not the browser's, so it rides on the session
  // rather than on this tab.
  const persistBrowserState = (patch: Record<string, unknown>) => {
    if (!workbenchSession || !api.updateWorkbenchBrowserState) return;
    const sessionId = workbenchSession.sessionId;
    const browserState = { ...workbenchSession.browserState, ...patch };
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

  const updateWorkbenchWrapLines = (wrapLines: boolean) => {
    setWorkbenchWrapLines(wrapLines);
    persistBrowserState({ sqlWrapLines: wrapLines });
  };


  // A draft seed counts as work: the model produced something, even if it is
  // not yet an immutable version.
  const sessionHasWork = Boolean(
    workbenchSession &&
      (workbenchSession.currentVersion !== null ||
        workbenchSession.draftSeed != null ||
        (workbenchTimeline?.turns.length ?? 0) > 0),
  );

  // The question is asked once per session. A session opened empty has not
  // been asked yet, so its question box stays live until it is.
  const questionIsLocked =
    state.kind === "preview" || state.kind === "polling" || sessionHasWork;

  const activeNotebookTurns = threadCells(workbenchTimeline, workbenchSession);

  // A run asked to be shown its result. The cell carrying it exists now, so
  // move to it: the outcome leads, whether the database returned rows or a
  // diagnostic, and editing again is a choice made from there.
  useEffect(() => {
    const turnId = revealTurnId.current;
    if (turnId !== null) {
      const failed = activeNotebookTurns.find((turn) => turn.turnId === turnId);
      if (failed) {
        revealTurnId.current = null;
        document
          .getElementById(`turn-${failed.ordinal}`)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
    const versionId = revealVersionId.current;
    if (versionId === null) return;
    const cell = activeNotebookTurns.find(
      (turn) => turn.selectedVersionId === versionId,
    );
    if (!cell) return;
    revealVersionId.current = null;
    document
      .getElementById(`turn-${cell.ordinal}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  const railStacked = viewportWidth < RAIL_STACK_BREAKPOINT;

  const activeDataSourceLabel =
    dataSources?.dataSources.find(
      (source) => source.id === effectiveDataSourceId,
    )?.label ??
    (effectiveDataSourceId || null);

  // Enough of the catalog to know what can be asked about, with the rail as
  // the way to the rest of it.
  const catalogSummary = (() => {
    const relations = (workbenchCatalog?.schemas ?? []).flatMap(
      (schema) => schema.views,
    );
    if (relations.length === 0) return null;
    const widest = relations.reduce((largest, view) =>
      view.columns.length > largest.columns.length ? view : largest,
    );
    return { relations: relations.length, widest };
  })();

  const catalogRelationCount = (workbenchCatalog?.schemas ?? []).reduce(
    (total, schema) => total + schema.views.length,
    0,
  );

  const railTurns: RailTurn[] = activeNotebookTurns.map((turn) => ({
    ordinal: turn.ordinal,
    instruction: turn.instruction,
    status:
      turn.status === "failed" || turn.execution?.status === "failed"
        ? "failed"
        : turn.execution?.status === "succeeded"
          ? "succeeded"
          : "not-run",
    current: Boolean(turn.current),
  }));

  const changeRailSection = (section: RailSection) => {
    // The two sections are mutually exclusive, and closing the open one leaves
    // the rail with nothing but its headers, so a second click on the open
    // section falls back to the thread rather than to an empty rail.
    const next: RailSection = section === railSection ? "turns" : section;
    setRailSection(next);
    persistBrowserState({ railSection: next });
  };

  const persistRailWidth = (width: number) => {
    persistBrowserState({ railWidth: width });
  };

  const openDetails = (turnId: string | null, tab: DetailsTab = "validation") => {
    setDetailsTurnId(turnId);
    setDetailsOpen(true);
    setDetailsTab(tab);
    if (turnId === null) return;
    const turn = activeNotebookTurns.find((item) => item.turnId === turnId);
    if (turn) setActiveTurnOrdinal(turn.ordinal);
    if (isManualCell(turnId)) return;
    if (generationEvidence?.turnId !== turnId) {
      void showWorkbenchGenerationEvidence(turnId);
    }
  };

  const detailsTurn =
    activeNotebookTurns.find((turn) => turn.turnId === detailsTurnId) ?? null;
  const detailsVersion =
    (detailsTurn
      ? workbenchSession?.versions.find(
          (version) => version.versionId === detailsTurn.selectedVersionId,
        )
      : workbenchSession?.currentVersion) ?? null;
  const detailsValidation =
    [...(workbenchSession?.validations ?? [])]
      .sort((left, right) => right.ordinal - left.ordinal)
      .find((validation) => validation.versionId === detailsVersion?.versionId) ??
    null;

  const refreshRecentSessions = () => {
    if (!api.listWorkbenchSessions) return;
    void api.listWorkbenchSessions()
      .then((response) => setRecentSessions(response.sessions))
      .catch(() => undefined);
  };

  const openSessionMenu = (menu: "closed" | "list" | "new" | "rename") => {
    setSessionMenu(menu);
    if (menu === "list") refreshRecentSessions();
    if (menu === "new") setDraftSessionName("");
    // Renaming starts from the name it already has.
    if (menu === "rename") setDraftSessionName(workbenchSession?.name ?? "");

  };

  const renameSession = (name: string) => {
    setSessionMenu("closed");
    const trimmed = name.trim();
    if (
      !workbenchSession ||
      !api.renameWorkbenchSession ||
      !trimmed ||
      trimmed === (workbenchSession.name ?? "")
    ) {
      return;
    }
    void api.renameWorkbenchSession(workbenchSession.sessionId, trimmed)
      .then((renamed) =>
        setWorkbenchSession((current) =>
          current?.sessionId === renamed.sessionId
            ? { ...current, name: renamed.name }
            : current,
        ),
      )
      .catch((error: unknown) => setWorkbenchError(messageFromError(error)));
  };

  const openRecentSession = (sessionId: string) => {
    setSessionMenu("closed");
    if (sessionId === workbenchSession?.sessionId || !api.getWorkbenchSession) {
      return;
    }
    void api.getWorkbenchSession(sessionId)
      .then((session) => {
        rememberActiveWorkbenchSession(session.sessionId);
        adoptWorkbenchSession(session);
      })
      .catch((error: unknown) => setWorkbenchError(messageFromError(error)));
  };

  const selectTurn = (ordinal: number) => {
    setActiveTurnOrdinal(ordinal);
    document
      .getElementById(`turn-${ordinal}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const activeGrounding = workbenchSession
    ? notebookGrounding(workbenchSession, workbenchSql, workbenchParameters)
    : null;
  const hasRefineDock = Boolean(
    usesNotebook && workbenchSession && workbenchTimeline,
  );
  const hasQueryDock = hasRefineDock || workbenchSession === null;

  const currentVersionRan = Boolean(
    workbenchSession?.currentVersionId &&
      executionForVersion(workbenchSession, workbenchSession.currentVersionId),
  );
  const editorDirty = workbenchSession?.currentVersion
    ? workbenchSql !== workbenchSession.currentVersion.sql
    : workbenchSql.trim().length > 0;
  // Open while there is something to do in it: a query not yet run, unsaved
  // edits, or an explicit ask to edit. Otherwise the run's result leads.
  const showEditor = editorOpen || editorDirty || !currentVersionRan;

  // The editable current query. It rides at the foot of the turn stack when
  // there is a thread to sit in, and stands alone when there is not.
  const workbenchPanel = sessionHasWork && workbenchSession ? (
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
          checkOutcome={
            workbenchSession.latestValidation &&
            workbenchSession.latestValidation.versionId ===
              workbenchSession.currentVersionId
              ? {
                  status: workbenchSession.latestValidation.status,
                  findings: workbenchSession.latestValidation.findings.length,
                }
              : null
          }
          onOpenValidationDetails={() => {
            const latest = activeNotebookTurns.at(-1);
            openDetails(latest?.turnId ?? null, "validation");
          }}
          sqlEditorFocusRequestId={sqlEditorFocusRequestId}
          // Each notebook cell renders the run recorded against its own query
          // version, so repeating the latest one here would show the same
          // result twice. Without the notebook this panel is the only surface
          // a failed run can appear on.
          showExecutionResult={
            !usesNotebook &&
            workbenchSession.executions.some(
              (execution) =>
                execution.status === "failed" &&
                execution.ordinal === Math.max(
                  ...workbenchSession.executions.map(
                    (candidate) => candidate.ordinal,
                  ),
                ),
            )
          }
          onSqlChange={setWorkbenchSql}
          onParametersChange={setWorkbenchParameters}
          onWrapLinesChange={updateWorkbenchWrapLines}
          onClearDraft={clearWorkbenchDraft}
          onRestoreCurrentVersion={restoreCurrentWorkbenchVersion}
          onRun={runWorkbenchDraft}
        />
  ) : null;

  const notebookShowing = Boolean(
    usesNotebook && sessionHasWork && workbenchSession && workbenchTimeline,
  );

  return (
    <div
      className={`dashboard-builder-shell${railStacked ? " dashboard-builder-shell--stacked" : ""}`}
      style={
        railStacked
          ? undefined
          : ({ "--dashboard-nav-width": `${railWidth}px` } as CSSProperties)
      }
    >
      <WorkbenchRail
        width={railWidth}
        stacked={railStacked}
        onWidthChange={setRailWidth}
        onWidthCommit={persistRailWidth}
        sessionName={
          workbenchSession
            ? (workbenchSession.name ?? "").trim() ||
              workbenchSession.question.trim() ||
              "New session"
            : null
        }
        sessionSourceLabel={activeDataSourceLabel}
        sessionMenu={sessionMenu}
        onSessionMenuChange={openSessionMenu}
        onRenameSession={renameSession}
        recentSessions={recentSessions}
        onOpenSession={openRecentSession}
        activeSessionId={workbenchSession?.sessionId ?? null}
        dataSources={dataSources?.dataSources ?? []}
        draftSessionName={draftSessionName}
        draftDataSourceId={dataSourceId}
        onDraftSessionNameChange={setDraftSessionName}
        onDraftDataSourceChange={setDataSourceId}
        onStartSession={startNewSession}
        newSessionDisabled={followupBusy || workbenchBusy !== null}
        openSection={railSection}
        onOpenSectionChange={changeRailSection}
        relationCount={catalogRelationCount}
        turns={railTurns}
        activeTurnOrdinal={activeTurnOrdinal}
        onSelectTurn={selectTurn}
        onOpenDetails={
          workbenchSession
            ? () =>
                openDetails(
                  (activeNotebookTurns.find(
                    (turn) => turn.ordinal === activeTurnOrdinal,
                  ) ?? activeNotebookTurns.at(-1))?.turnId ?? null,
                )
            : undefined
        }
        detailsOpen={detailsOpen}
        themePreference={themePreference}
        onThemePreferenceChange={onThemePreferenceChange ?? (() => undefined)}
        activeSection={activeSection}
        onSectionChange={setActiveSection}
      >
        <DatasetBrowser
          api={api}
          catalog={workbenchCatalog}
          catalogLoadingFailed={workbenchCatalogFailed}
          dataSourceId={effectiveDataSourceId || undefined}
        />
      </WorkbenchRail>

      <main
        className={`app-shell${hasQueryDock && activeSection === "ask" ? " app-shell--with-query-dock" : ""}`}
      >
        <section hidden={activeSection !== "ask"} aria-labelledby="question-title">
          {/*
            Datasets, Widgets and Dashboards each state where you are; this
            screen said nothing, so the one you spend the most time in was the
            one that never named itself. The eyebrow names the section — the
            same word the nav uses — and the heading names the session, which
            is the thing on screen.
          */}
          <header className="workbench-header">
            {workbenchSession && <p className="eyebrow">Workbench</p>}
            <h1 id="question-title" tabIndex={-1}>
              {workbenchSession
                ? (workbenchSession.name ?? "").trim() ||
                  workbenchSession.question.trim() ||
                  "New session"
                : "Workbench"}
            </h1>
          </header>

          {workbenchSession && (
            // Rendered into the demo banner's trailing edge. The banner is a
            // sibling of this workspace, so it is positioned there rather
            // than lifting session state out of the workspace to reach it.
            <div className="dashboard-session-meta">
              Session {workbenchSession.sessionId.slice(0, 8)}
              {workbenchTimeline
                ? ` · ${workbenchTimeline.turns.length} turn${
                    workbenchTimeline.turns.length === 1 ? "" : "s"
                  }`
                : ""}
            </div>
          )}

      {!sessionHasWork && state.kind !== "submitting" && (
        /*
          The rail names the product and the composer holds the question, so
          this says only what neither can: what a session is for, and that
          nothing leaves it without review.
        */
        <div className="workbench-empty">
          <p className="workbench-empty__lead">
            Ask a question about {activeDataSourceLabel ?? "the connected data"}.
          </p>
          <p>
            Catalyst writes SQL you can read and edit, runs it, and keeps every
            version. Nothing is saved until you review it.
          </p>
          {catalogSummary && (
            <div className="workbench-empty__catalog">
              <dl>
                <div>
                  <dt>Relations</dt>
                  <dd>{catalogSummary.relations}</dd>
                </div>
                <div>
                  <dt>Largest</dt>
                  <dd>
                    <code>{catalogSummary.widest.qualifiedName}</code>
                  </dd>
                </div>
                <div>
                  <dt>Columns</dt>
                  <dd>{catalogSummary.widest.columns.length}</dd>
                </div>
              </dl>
              <p>{catalogSummary.widest.grain}</p>
              <button
                type="button"
                onClick={() => changeRailSection("data")}
              >
                Browse every relation and column in DATA →
              </button>
            </div>
          )}
        </div>
      )}

      {!sessionHasWork && (
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

      {notebookShowing && workbenchSession && workbenchTimeline && (
        <TurnNotebook
          turns={activeNotebookTurns}
          session={workbenchSession}
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
          lastRunFailed={latestExecutionFailed(workbenchSession)}
          onInstructionChange={setFollowupInstruction}
          onProfileChange={setProfileId}
          onGenerate={generateNextWorkbenchQuery}
          onOpenDetails={openDetails}
          onSaveDataset={() => openDatasetReview.current?.()}
          activeCell={
            showEditor ? (
              workbenchPanel
            ) : (
              <div className="query-turn__next">
                <p>
                  {latestExecutionFailed(workbenchSession)
                    ? "That run failed. The diagnostic is above — fix the query by hand, or say what to change below."
                    : "Ask for the next query below, or edit this one by hand."}
                </p>
                <Button
                  type="button"
                  kind="tertiary"
                  size="sm"
                  onClick={() => setEditorOpen(true)}
                >
                  Edit query
                </Button>
              </div>
            )
          }
        />
      )}


      {!notebookShowing && workbenchPanel}

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

        {workbenchSession && detailsOpen && (
          <DetailsPanel
            session={workbenchSession}
            turnOrdinal={detailsTurn?.ordinal ?? null}
            version={detailsVersion}
            validation={detailsValidation}
            evidence={
              detailsTurn && generationEvidence?.turnId === detailsTurn.turnId
                ? generationEvidence
                : null
            }
            evidenceLoading={
              detailsTurn !== null &&
              generationEvidenceLoadingTurnId === detailsTurn.turnId
            }
            evidenceError={generationEvidenceError}
            tab={detailsTab}
            developerMode={developerMode}
            stacked={railStacked}
            railWidth={railWidth}
            onTabChange={setDetailsTab}
            onDeveloperModeChange={setDeveloperMode}
            onClose={() => {
              setDetailsOpen(false);
              setDetailsTurnId(null);
            }}
          />
        )}

        <DashboardPublishPanel
          api={api}
          hostedInThread={notebookShowing || !sessionHasWork}
          registerDatasetOpener={registerDatasetOpener}
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
