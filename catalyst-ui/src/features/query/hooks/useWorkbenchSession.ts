import { useEffect, useState } from "react";
import type { CatalystApi } from "../api";
import type {
  CatalystExecutionOutcome,
  CatalystPolicyOutcome,
  CatalystPreview,
  CatalystQueryOutcome,
  CatalystTable,
  DataSourcesResponse,
  QueryOptions,
  WorkbenchSession,
  WorkbenchTurnTimeline,
} from "../types";

export type WorkflowState =
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

export const writeDataSourceIdToUrl = (dataSourceId: string) => {
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

/**
 * The session and what grounds it: the question, the workflow state, the
 * profile and data-source options the gateway offers, the open workbench
 * session and its turn timeline.
 *
 * One of five hooks extracted from QueryWorkspace. Adopting a session is a
 * cross-cluster workflow (it restores the editor buffer and the rail layout
 * too), so it stays with the component; this hook owns the state adopted
 * into and the option lists everything selects from.
 */
export const useWorkbenchSession = (api: CatalystApi) => {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<WorkflowState>({ kind: "idle" });
  const [queryOptions, setQueryOptions] = useState<QueryOptions | null>(null);
  const [profileId, setProfileId] = useState("");
  const [dataSources, setDataSources] = useState<DataSourcesResponse | null>(
    null,
  );
  // What the picker holds before a session exists. Once a session exists its
  // own source wins — see effectiveDataSourceId in the component.
  const [dataSourceId, setDataSourceId] = useState(readDataSourceIdFromUrl);
  const [workbenchSession, setWorkbenchSession] =
    useState<WorkbenchSession | null>(null);
  const [workbenchTimeline, setWorkbenchTimeline] =
    useState<WorkbenchTurnTimeline | null>(null);

  useEffect(() => {
    if (!api.getQueryOptions) return;
    const controller = new AbortController();
    api.getQueryOptions(controller.signal)
      .then((options) => {
        setQueryOptions(options);
        const defaultProfile = options.profiles.find(
          (profile) =>
            profile.id === options.defaultProfileId && profile.available,
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

  const usesWorkbench = Boolean(
    api.createWorkbenchSession &&
      api.createWorkbenchVersion &&
      api.executeWorkbenchVersion,
  );
  const usesNotebook = Boolean(api.createWorkbenchTurn && api.getWorkbenchTurns);
  const availableProfiles =
    queryOptions?.profiles.filter((profile) => profile.available) ?? [];
  const fallbackProfileId =
    availableProfiles.find(
      (profile) => profile.id === queryOptions?.defaultProfileId,
    )?.id ??
    availableProfiles[0]?.id ??
    "";
  const selectedAvailableProfileId = availableProfiles.some(
    (profile) => profile.id === profileId,
  )
    ? profileId
    : fallbackProfileId;
  const revisionProfiles =
    queryOptions?.profiles.filter(
      (profile) => profile.available && profile.revisionCapable === true,
    ) ?? [];
  const noAvailableProfiles =
    queryOptions !== null &&
    !queryOptions.profiles.some((profile) => profile.available);
  const fallbackRevisionProfileId =
    revisionProfiles.find(
      (profile) => profile.id === queryOptions?.defaultProfileId,
    )?.id ??
    revisionProfiles[0]?.id ??
    "";
  const selectedRevisionProfileId = revisionProfiles.some(
    (profile) => profile.id === profileId,
  )
    ? profileId
    : fallbackRevisionProfileId;

  return {
    question,
    setQuestion,
    state,
    setState,
    queryOptions,
    profileId,
    setProfileId,
    dataSources,
    dataSourceId,
    setDataSourceId,
    workbenchSession,
    setWorkbenchSession,
    workbenchTimeline,
    setWorkbenchTimeline,
    usesWorkbench,
    usesNotebook,
    availableProfiles,
    selectedAvailableProfileId,
    revisionProfiles,
    noAvailableProfiles,
    selectedRevisionProfileId,
  };
};
