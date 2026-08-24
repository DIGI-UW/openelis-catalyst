import type {
  CatalystExecutionResponse,
  CatalystPreview,
  CatalystSubmission,
  DataSourcesResponse,
  DatasetOverview,
  DatasetRows,
  QueryOptions,
  WorkbenchEditorCatalog,
  WorkbenchExecution,
  WorkbenchGenerationEvidence,
  WorkbenchSession,
  WorkbenchSessionList,
  WorkbenchTurn,
  WorkbenchTurnRequest,
  WorkbenchTurnTimeline,
  WorkbenchValidation,
  WorkbenchVersionDraft,
  DashboardBuilderEntity,
  DashboardBuilderCollection,
  DashboardPresentationKind,
  DashboardPublication,
} from "./types";

export interface CatalystApi {
  submitQuestion(
    question: string,
    profileId?: string,
    signal?: AbortSignal,
  ): Promise<CatalystSubmission>;
  executePreview(
    preview: CatalystPreview,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CatalystExecutionResponse>;
  pollExecution(
    previewId: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CatalystExecutionResponse>;
  getQueryOptions?(signal?: AbortSignal): Promise<QueryOptions>;
  getDataSources?(signal?: AbortSignal): Promise<DataSourcesResponse>;
  getDatasetOverview?(
    dataSourceId?: string,
    signal?: AbortSignal,
  ): Promise<DatasetOverview>;
  getDatasetRows?(
    filters?: {
      testName?: string;
      patientId?: string;
      limit?: number;
      offset?: number;
      dataSourceId?: string;
    },
    signal?: AbortSignal,
  ): Promise<DatasetRows>;
  createWorkbenchSession?(
    question: string,
    profileId?: string,
    browserState?: Record<string, unknown>,
    dataSourceId?: string,
    signal?: AbortSignal,
    name?: string,
  ): Promise<WorkbenchSession>;
  listWorkbenchSessions?(signal?: AbortSignal): Promise<WorkbenchSessionList>;
  renameWorkbenchSession?(
    sessionId: string,
    name: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchSession>;
  /** Ask the first question of a session that was opened empty. */
  askWorkbenchSessionQuestion?(
    sessionId: string,
    question: string,
    profileId?: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchSession>;
  getWorkbenchSession?(
    sessionId: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchSession>;
  pinWorkbenchGuidance?(
    sessionId: string,
    text: string,
  ): Promise<WorkbenchSession>;
  unpinWorkbenchGuidance?(
    sessionId: string,
    entryId: string,
  ): Promise<WorkbenchSession>;
  createWorkbenchTurn?(
    sessionId: string,
    request: WorkbenchTurnRequest,
    signal?: AbortSignal,
  ): Promise<WorkbenchTurn>;
  getWorkbenchTurns?(
    sessionId: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchTurnTimeline>;
  getWorkbenchGenerationEvidence?(
    sessionId: string,
    turnId: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchGenerationEvidence>;
  getWorkbenchCatalog?(
    dataSourceId?: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchEditorCatalog>;
  createWorkbenchVersion?(
    sessionId: string,
    draft: WorkbenchVersionDraft,
    signal?: AbortSignal,
  ): Promise<WorkbenchSession>;
  validateWorkbenchVersion?(
    versionId: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchValidation>;
  executeWorkbenchVersion?(
    versionId: string,
    queryDigest: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<WorkbenchExecution>;
  updateWorkbenchBrowserState?(
    sessionId: string,
    browserState: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<WorkbenchSession>;
  listDashboardDatasets?(signal?: AbortSignal): Promise<DashboardBuilderCollection>;
  listDashboardWidgets?(signal?: AbortSignal): Promise<DashboardBuilderCollection>;
  listDashboards?(signal?: AbortSignal): Promise<DashboardBuilderCollection>;
  saveDashboardDataset?(
    input: { sessionId: string; executionId: string; title?: string },
    signal?: AbortSignal,
  ): Promise<DashboardBuilderEntity>;
  saveDashboardWidget?(
    input: {
      datasetVersionId: string;
      title?: string;
      presentationKind?: DashboardPresentationKind;
    },
    signal?: AbortSignal,
  ): Promise<DashboardBuilderEntity>;
  saveDashboard?(
    input: { title?: string; widgetVersionIds: string[] },
    signal?: AbortSignal,
  ): Promise<DashboardBuilderEntity>;
  publishDashboard?(dashboardVersionId: string, signal?: AbortSignal): Promise<DashboardPublication>;
  getDashboardPublication?(
    dashboardVersionId: string,
    signal?: AbortSignal,
  ): Promise<DashboardPublication>;
}

export class CatalystApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "CatalystApiError";
    this.status = status;
  }
}

interface CatalystApiOptions {
  baseUrl?: string;
  fetcher?: typeof fetch;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const errorMessage = (body: unknown, status: number) => {
  if (isRecord(body)) {
    if (typeof body.detail === "string") return body.detail;
    if (typeof body.message === "string") return body.message;
    if (isRecord(body.error) && typeof body.error.message === "string") {
      return body.error.message;
    }
  }
  return `Catalyst request failed (HTTP ${status}).`;
};

const parseJson = async (response: Response): Promise<unknown> => {
  const text = await response.text();
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new CatalystApiError(
      `Catalyst returned an invalid response (HTTP ${response.status}).`,
      response.status,
    );
  }
};

const isSubmission = (body: unknown): body is CatalystSubmission =>
  isRecord(body) &&
  (body.contractVersion === "catalyst.preview.v1" ||
    body.contractVersion === "catalyst.query.v1" ||
    body.contractVersion === "catalyst.policy.outcome.v1");

const isExecutionResponse = (
  body: unknown,
): body is CatalystExecutionResponse =>
  isRecord(body) &&
  (body.contractVersion === "catalyst.table.v1" ||
    body.contractVersion === "catalyst.execution.outcome.v1");

const hasContractVersion = <Version extends string>(
  body: unknown,
  contractVersion: Version,
): body is Record<string, unknown> & { contractVersion: Version } =>
  isRecord(body) && body.contractVersion === contractVersion;

const isDashboardEntity = (body: unknown): body is DashboardBuilderEntity =>
  isRecord(body) &&
  typeof body.id === "string" &&
  typeof body.versionId === "string" &&
  typeof body.configurationDigest === "string";

const isDashboardCollection = (
  body: unknown,
  kind: DashboardBuilderCollection["kind"],
): body is DashboardBuilderCollection =>
  isRecord(body) &&
  body.contractVersion === "catalyst.dashboard-builder.v1" &&
  body.kind === kind &&
  Array.isArray(body.items) &&
  body.items.every(isDashboardEntity);

const isDashboardPublication = (body: unknown): body is DashboardPublication =>
  isRecord(body) &&
  ["bundle_ready", "imported", "import_failed"].includes(String(body.status)) &&
  isRecord(body.pointer) &&
  isRecord(body.pointer.bundle) &&
  typeof body.pointer.bundle.fileName === "string";

export const createCatalystApi = ({
  baseUrl = "/v1/catalyst",
  fetcher = globalThis.fetch,
}: CatalystApiOptions = {}): CatalystApi => {
  const root = baseUrl.replace(/\/+$/, "");

  return {
    async submitQuestion(question, profileId, signal) {
      const response = await fetcher(`${root}/queries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.question.request.v1",
          deploymentMode: "demo",
          question,
          ...(profileId ? { profileId } : {}),
        }),
        signal,
      });
      const body = await parseJson(response);
      if (!isSubmission(body)) {
        throw new CatalystApiError(
          errorMessage(body, response.status),
          response.status,
        );
      }
      return body;
    },

    async executePreview(preview, idempotencyKey, signal) {
      const response = await fetcher(
        `${root}/previews/${encodeURIComponent(preview.previewId)}/execute`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contractVersion: "catalyst.execute.request.v1",
            previewId: preview.previewId,
            queryDigest: preview.queryDigest,
            accept: true,
            idempotencyKey,
          }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!isExecutionResponse(body)) {
        throw new CatalystApiError(
          errorMessage(body, response.status),
          response.status,
        );
      }
      return body;
    },

    async pollExecution(previewId, idempotencyKey, signal) {
      const parameters = new URLSearchParams({ idempotencyKey });
      const response = await fetcher(
        `${root}/executions/${encodeURIComponent(previewId)}?${parameters.toString()}`,
        {
          method: "GET",
          headers: { Accept: "application/json" },
          signal,
        },
      );
      const body = await parseJson(response);
      if (!isExecutionResponse(body)) {
        throw new CatalystApiError(
          errorMessage(body, response.status),
          response.status,
        );
      }
      return body;
    },

    async getQueryOptions(signal) {
      const response = await fetcher(`${root}/query-options`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isRecord(body) || body.contractVersion !== "catalyst.query-options.v1") {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as QueryOptions;
    },

    async getDataSources(signal) {
      const response = await fetcher(`${root}/data-sources`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isRecord(body) || body.contractVersion !== "catalyst.data-sources.v1") {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as DataSourcesResponse;
    },

    async getDatasetOverview(dataSourceId, signal) {
      const suffix = dataSourceId
        ? `?dataSourceId=${encodeURIComponent(dataSourceId)}`
        : "";
      const response = await fetcher(`${root}/dataset${suffix}`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isRecord(body) || body.contractVersion !== "catalyst.dataset-overview.v1") {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as DatasetOverview;
    },

    async getDatasetRows(filters = {}, signal) {
      const parameters = new URLSearchParams();
      if (filters.testName) parameters.set("testName", filters.testName);
      if (filters.patientId) parameters.set("patientId", filters.patientId);
      if (filters.dataSourceId) parameters.set("dataSourceId", filters.dataSourceId);
      parameters.set("limit", String(filters.limit ?? 25));
      parameters.set("offset", String(filters.offset ?? 0));
      const response = await fetcher(`${root}/dataset/rows?${parameters.toString()}`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isRecord(body) || body.contractVersion !== "catalyst.dataset-rows.v1") {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as DatasetRows;
    },

    async createWorkbenchSession(question, profileId, browserState, dataSourceId, signal, name) {
      const response = await fetcher(`${root}/workbench/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.workbench.session.request.v1",
          deploymentMode: "demo",
          question,
          ...(name ? { name } : {}),
          ...(profileId ? { profileId } : {}),
          ...(dataSourceId ? { dataSourceId } : {}),
          ...(browserState ? { browserState } : {}),
        }),
        signal,
      });
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async askWorkbenchSessionQuestion(sessionId, question, profileId, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/question`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            ...(profileId ? { profileId } : {}),
          }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async renameWorkbenchSession(sessionId, name, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/name`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async listWorkbenchSessions(signal) {
      // The menu is for resuming recent work, not browsing an archive.
      const response = await fetcher(`${root}/workbench/sessions?limit=10`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session-list.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSessionList;
    },

    async getWorkbenchSession(sessionId, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}`,
        {
          headers: { Accept: "application/json" },
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async pinWorkbenchGuidance(sessionId, text) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/guidance`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contractVersion: "catalyst.workbench.guidance.request.v1",
            text,
          }),
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },
    async unpinWorkbenchGuidance(sessionId, entryId) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}` +
          `/guidance/${encodeURIComponent(entryId)}`,
        { method: "DELETE" },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },
    async createWorkbenchTurn(sessionId, request, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/turns`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.turn.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchTurn;
    },

    async getWorkbenchTurns(sessionId, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/turns`,
        {
          headers: { Accept: "application/json" },
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.turn.timeline.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchTurnTimeline;
    },

    async getWorkbenchGenerationEvidence(sessionId, turnId, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/generation-evidence`,
        {
          headers: { Accept: "application/json" },
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.generation-evidence.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchGenerationEvidence;
    },

    async getWorkbenchCatalog(dataSourceId, signal) {
      const suffix = dataSourceId
        ? `?dataSourceId=${encodeURIComponent(dataSourceId)}`
        : "";
      const response = await fetcher(`${root}/workbench/catalog${suffix}`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.editor-catalog.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchEditorCatalog;
    },

    async createWorkbenchVersion(sessionId, draft, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/versions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contractVersion: "catalyst.workbench.version.request.v1",
            ...(draft.parentVersionId
              ? { parentVersionId: draft.parentVersionId }
              : {}),
            ...(draft.parentQueryDigest
              ? { parentQueryDigest: draft.parentQueryDigest }
              : {}),
            sql: draft.sql,
            parameters: draft.parameters,
            ...(draft.expectedColumns
              ? { expectedColumns: draft.expectedColumns }
              : {}),
            ...(draft.dataSourceId ? { dataSourceId: draft.dataSourceId } : {}),
          }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async validateWorkbenchVersion(versionId, signal) {
      const response = await fetcher(
        `${root}/workbench/versions/${encodeURIComponent(versionId)}/validate`,
        {
          method: "POST",
          headers: { Accept: "application/json" },
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.validation.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchValidation;
    },

    async executeWorkbenchVersion(
      versionId,
      queryDigest,
      idempotencyKey,
      signal,
    ) {
      const response = await fetcher(
        `${root}/workbench/versions/${encodeURIComponent(versionId)}/execute`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contractVersion: "catalyst.workbench.execute.request.v1",
            versionId,
            queryDigest,
            idempotencyKey,
          }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.execution.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchExecution;
    },

    async updateWorkbenchBrowserState(sessionId, browserState, signal) {
      const response = await fetcher(
        `${root}/workbench/sessions/${encodeURIComponent(sessionId)}/browser-state`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ browserState }),
          signal,
        },
      );
      const body = await parseJson(response);
      if (!hasContractVersion(body, "catalyst.workbench.session.v1")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body as unknown as WorkbenchSession;
    },

    async listDashboardDatasets(signal) {
      const response = await fetcher(`${root}/dashboard-builder/datasets`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardCollection(body, "dataset")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async listDashboardWidgets(signal) {
      const response = await fetcher(`${root}/dashboard-builder/widgets`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardCollection(body, "widget")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async listDashboards(signal) {
      const response = await fetcher(`${root}/dashboard-builder/dashboards`, {
        headers: { Accept: "application/json" },
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardCollection(body, "dashboard")) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async saveDashboardDataset(input, signal) {
      const response = await fetcher(`${root}/dashboard-builder/datasets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardEntity(body)) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async saveDashboardWidget(input, signal) {
      const response = await fetcher(`${root}/dashboard-builder/widgets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardEntity(body)) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async saveDashboard(input, signal) {
      const response = await fetcher(`${root}/dashboard-builder/dashboards`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal,
      });
      const body = await parseJson(response);
      if (!isDashboardEntity(body)) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async publishDashboard(dashboardVersionId, signal) {
      const response = await fetcher(
        `${root}/dashboard-builder/dashboards/${encodeURIComponent(dashboardVersionId)}/publish`,
        { method: "POST", headers: { Accept: "application/json" }, signal },
      );
      const body = await parseJson(response);
      if (!isDashboardPublication(body)) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },

    async getDashboardPublication(dashboardVersionId, signal) {
      const response = await fetcher(
        `${root}/dashboard-builder/dashboards/${encodeURIComponent(dashboardVersionId)}/publication`,
        { headers: { Accept: "application/json" }, signal },
      );
      const body = await parseJson(response);
      if (!isDashboardPublication(body)) {
        throw new CatalystApiError(errorMessage(body, response.status), response.status);
      }
      return body;
    },
  };
};

export const catalystApi = createCatalystApi();
