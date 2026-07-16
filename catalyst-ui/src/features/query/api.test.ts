import { beforeEach, describe, expect, it, vi } from "vitest";
import { createCatalystApi } from "./api";
import {
  executionOutcome,
  policyOutcome,
  preview,
  QUESTION,
  table,
} from "./test/fixtures";

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("Catalyst API client", () => {
  const fetcher = vi.fn<typeof fetch>();
  const api = createCatalystApi({
    baseUrl: "/v1/catalyst",
    fetcher,
  });

  beforeEach(() => {
    fetcher.mockReset();
  });

  it("posts the versioned demo question request", async () => {
    fetcher.mockResolvedValue(jsonResponse(preview, 201));

    await expect(api.submitQuestion(QUESTION)).resolves.toEqual(preview);

    expect(fetcher).toHaveBeenCalledWith("/v1/catalyst/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contractVersion: "catalyst.question.request.v1",
        deploymentMode: "demo",
        question: QUESTION,
      }),
      signal: undefined,
    });
  });

  it("returns a versioned policy rejection from a non-2xx response", async () => {
    fetcher.mockResolvedValue(jsonResponse(policyOutcome, 422));

    await expect(api.submitQuestion(QUESTION)).resolves.toEqual(policyOutcome);
  });

  it("accepts the exact preview with an idempotency key", async () => {
    fetcher.mockResolvedValue(jsonResponse(table));

    await expect(
      api.executePreview(preview, "key with spaces"),
    ).resolves.toEqual(table);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/previews/${encodeURIComponent(preview.previewId)}/execute`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.execute.request.v1",
          previewId: preview.previewId,
          queryDigest: preview.queryDigest,
          accept: true,
          idempotencyKey: "key with spaces",
        }),
        signal: undefined,
      },
    );
  });

  it("polls without starting a new execution", async () => {
    const outcome = executionOutcome("in_progress");
    fetcher.mockResolvedValue(jsonResponse(outcome, 202));

    await expect(
      api.pollExecution(preview.previewId, "key with spaces"),
    ).resolves.toEqual(outcome);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/executions/${encodeURIComponent(preview.previewId)}?idempotencyKey=key+with+spaces`,
      {
        method: "GET",
        headers: { Accept: "application/json" },
        signal: undefined,
      },
    );
  });

  it("rejects an incompatible response instead of guessing at its shape", async () => {
    fetcher.mockResolvedValue(jsonResponse({ detail: "Bad gateway" }, 502));

    await expect(api.submitQuestion(QUESTION)).rejects.toThrow("Bad gateway");
  });

  it("reports an invalid non-JSON response", async () => {
    fetcher.mockResolvedValue(
      new Response("<html>proxy error</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );

    await expect(api.submitQuestion(QUESTION)).rejects.toThrow(
      "Catalyst returned an invalid response (HTTP 502).",
    );
  });
});
