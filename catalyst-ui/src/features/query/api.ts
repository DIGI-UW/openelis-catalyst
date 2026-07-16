import type {
  CatalystExecutionResponse,
  CatalystPreview,
  CatalystSubmission,
} from "./types";

export interface CatalystApi {
  submitQuestion(
    question: string,
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

export const createCatalystApi = ({
  baseUrl = "/v1/catalyst",
  fetcher = globalThis.fetch,
}: CatalystApiOptions = {}): CatalystApi => {
  const root = baseUrl.replace(/\/+$/, "");

  return {
    async submitQuestion(question, signal) {
      const response = await fetcher(`${root}/queries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.question.request.v1",
          deploymentMode: "demo",
          question,
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
  };
};

export const catalystApi = createCatalystApi();
