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

const workbenchSession = {
  contractVersion: "catalyst.workbench.session.v1" as const,
  sessionId: "2bed91de-fa7d-4ffa-b4ae-0a454a883930",
  question: QUESTION,
  profileId: "catalyst-query-gemma-e4b",
  datasetId: "analytics",
  datasetVersion: "pipeline-1",
  catalogVersion: "catalog-v1",
  currentVersionId: "d801dc1d-fc94-435b-bee6-2b45c3173af1",
  browserState: {},
  provenance: {},
  status: "active",
  createdAt: "2026-07-17T00:00:00Z",
  updatedAt: "2026-07-17T00:00:00Z",
  versions: [],
  currentVersion: null,
  validations: [],
  latestValidation: null,
  executions: [],
};

const editorCatalog = {
  contractVersion: "catalyst.workbench.editor-catalog.v1" as const,
  catalogVersion: "catalog-v1",
  schemaVersion: "schema-v1",
  dialect: "postgresql" as const,
  schemas: [
    {
      name: "analytics",
      views: [
        {
          name: "lab_result_fact_v1",
          qualifiedName: "analytics.lab_result_fact_v1",
          grain: "One row per FHIR Observation.",
          columns: [{
            name: "patient_id",
            logicalType: "string",
            nullable: false,
            description: "FHIR Patient resource identifier.",
          }],
        },
      ],
    },
  ],
};

const turnRequest = {
  contractVersion: "catalyst.workbench.turn.request.v1" as const,
  instruction: "Only include released viral load results.",
  profileId: "catalyst-query-gemma-4-12b-qwen-review",
  observedBase: {
    versionId: "d801dc1d-fc94-435b-bee6-2b45c3173af1",
    queryDigest: "a".repeat(64),
  },
  editorSnapshot: {
    contractVersion: "catalyst.workbench.editor-snapshot.v1" as const,
    sql: "SELECT 1",
    parameters: [],
    expectedColumns: [],
    editorDigest:
      "82d9696f92e64acb0c4edba843633c97eb23fd3f22887d93755eb86971855105",
  },
};

const completedTurn = {
  contractVersion: "catalyst.workbench.turn.v1" as const,
  sessionId: workbenchSession.sessionId,
  turnId: "9e32d2d4-c74c-4d6f-b352-502e221b5b14",
  status: "completed" as const,
};

const turnTimeline = {
  contractVersion: "catalyst.workbench.turn.timeline.v1" as const,
  sessionId: workbenchSession.sessionId,
  turns: [completedTurn],
  currentTurnId: completedTurn.turnId,
  currentVersion: turnRequest.observedBase,
};

const generationEvidence = {
  contractVersion: "catalyst.workbench.generation-evidence.v1" as const,
  evidenceId: "b3d8b838-98ff-4743-875b-69e7204c8218",
  sessionId: workbenchSession.sessionId,
  turnId: completedTurn.turnId,
  invocations: [],
};

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

  it("submits the selected Hub profile without local model configuration", async () => {
    fetcher.mockResolvedValue(jsonResponse(preview, 201));

    await api.submitQuestion(QUESTION, "catalyst-query-gemma-e4b");

    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      contractVersion: "catalyst.question.request.v1",
      deploymentMode: "demo",
      question: QUESTION,
      profileId: "catalyst-query-gemma-e4b",
    });
  });

  it("creates a manual workbench session through the real Hub profile", async () => {
    fetcher.mockResolvedValue(jsonResponse(workbenchSession, 201));

    await expect(
      api.createWorkbenchSession?.(QUESTION, "catalyst-query-gemma-e4b"),
    ).resolves.toEqual(workbenchSession);

    expect(fetcher).toHaveBeenCalledWith("/v1/catalyst/workbench/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contractVersion: "catalyst.workbench.session.request.v1",
        deploymentMode: "demo",
        question: QUESTION,
        profileId: "catalyst-query-gemma-e4b",
      }),
      signal: undefined,
    });
  });

  it("loads completion identifiers from the gateway-owned editor catalog", async () => {
    fetcher.mockResolvedValue(jsonResponse(editorCatalog));

    await expect(api.getWorkbenchCatalog?.()).resolves.toEqual(editorCatalog);

    expect(fetcher).toHaveBeenCalledWith("/v1/catalyst/workbench/catalog", {
      headers: { Accept: "application/json" },
      signal: undefined,
    });
  });

  it("restores persisted dashboard-builder collections", async () => {
    const dataset = {
      id: "dataset-1",
      versionId: "dataset-v1",
      ordinal: 1,
      configuration: {},
      configurationDigest: "a".repeat(64),
      createdAt: "2026-08-06T00:00:00Z",
    };
    fetcher.mockResolvedValue(
      jsonResponse({
        contractVersion: "catalyst.dashboard-builder.v1",
        kind: "dataset",
        items: [dataset],
      }),
    );

    await expect(api.listDashboardDatasets?.()).resolves.toEqual({
      contractVersion: "catalyst.dashboard-builder.v1",
      kind: "dataset",
      items: [dataset],
    });

    expect(fetcher).toHaveBeenCalledWith("/v1/catalyst/dashboard-builder/datasets", {
      headers: { Accept: "application/json" },
      signal: undefined,
    });
  });

  it("restores a persisted workbench session by ID", async () => {
    fetcher.mockResolvedValue(jsonResponse(workbenchSession));

    await expect(
      api.getWorkbenchSession?.(workbenchSession.sessionId),
    ).resolves.toEqual(workbenchSession);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/workbench/sessions/${workbenchSession.sessionId}`,
      {
        headers: { Accept: "application/json" },
        signal: undefined,
      },
    );
  });

  it("submits the exact visible editor snapshot as a contextual follow-up", async () => {
    fetcher.mockResolvedValue(jsonResponse(completedTurn, 201));

    await expect(
      api.createWorkbenchTurn?.(workbenchSession.sessionId, turnRequest),
    ).resolves.toEqual(completedTurn);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/workbench/sessions/${workbenchSession.sessionId}/turns`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(turnRequest),
        signal: undefined,
      },
    );
  });

  it("restores the compact chronological turn timeline", async () => {
    fetcher.mockResolvedValue(jsonResponse(turnTimeline));

    await expect(
      api.getWorkbenchTurns?.(workbenchSession.sessionId),
    ).resolves.toEqual(turnTimeline);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/workbench/sessions/${workbenchSession.sessionId}/turns`,
      { headers: { Accept: "application/json" }, signal: undefined },
    );
  });

  it("loads typed generation evidence only when the user asks for detail", async () => {
    fetcher.mockResolvedValue(jsonResponse(generationEvidence));

    await expect(
      api.getWorkbenchGenerationEvidence?.(
        workbenchSession.sessionId,
        completedTurn.turnId,
      ),
    ).resolves.toEqual(generationEvidence);

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/workbench/sessions/${workbenchSession.sessionId}/turns/${completedTurn.turnId}/generation-evidence`,
      { headers: { Accept: "application/json" }, signal: undefined },
    );
  });

  it("persists the exact SQL buffer as an immutable human child version", async () => {
    fetcher.mockResolvedValue(jsonResponse(workbenchSession, 201));
    const sql = "SELECT patient_id FROM analytics.lab_result_fact_v1";

    await api.createWorkbenchVersion?.(workbenchSession.sessionId, {
      parentVersionId: "version-parent",
      parentQueryDigest: "a".repeat(64),
      sql,
      parameters: [
        { name: "test_name", type: "string", source: "human", value: "Viral Load" },
      ],
      expectedColumns: [],
    });

    expect(fetcher).toHaveBeenCalledWith(
      `/v1/catalyst/workbench/sessions/${workbenchSession.sessionId}/versions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.workbench.version.request.v1",
          parentVersionId: "version-parent",
          parentQueryDigest: "a".repeat(64),
          sql,
          parameters: [
            {
              name: "test_name",
              type: "string",
              source: "human",
              value: "Viral Load",
            },
          ],
          expectedColumns: [],
        }),
        signal: undefined,
      },
    );
  });

  it("revalidates and runs an exact immutable version without a validation gate", async () => {
    const validation = {
      contractVersion: "catalyst.workbench.validation.v1",
      validationId: "validation-1",
      status: "invalid",
    };
    const execution = {
      contractVersion: "catalyst.workbench.execution.v1",
      executionId: "execution-1",
      status: "failed",
    };
    fetcher
      .mockResolvedValueOnce(jsonResponse(validation, 201))
      .mockResolvedValueOnce(jsonResponse(execution));

    await expect(
      api.validateWorkbenchVersion?.("version-1"),
    ).resolves.toEqual(validation);
    await expect(
      api.executeWorkbenchVersion?.("version-1", "b".repeat(64), "run-1"),
    ).resolves.toEqual(execution);

    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/v1/catalyst/workbench/versions/version-1/validate",
      {
        method: "POST",
        headers: { Accept: "application/json" },
        signal: undefined,
      },
    );
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/v1/catalyst/workbench/versions/version-1/execute",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contractVersion: "catalyst.workbench.execute.request.v1",
          versionId: "version-1",
          queryDigest: "b".repeat(64),
          idempotencyKey: "run-1",
        }),
        signal: undefined,
      },
    );
  });

  it("reads dataset overview and filtered rows", async () => {
    fetcher
      .mockResolvedValueOnce(
        jsonResponse({ contractVersion: "catalyst.dataset-overview.v1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ contractVersion: "catalyst.dataset-rows.v1" }),
      );

    await api.getDatasetOverview?.();
    await api.getDatasetRows?.({ testName: "Viral Load", limit: 25, offset: 25 });

    expect(fetcher).toHaveBeenNthCalledWith(1, "/v1/catalyst/dataset", {
      headers: { Accept: "application/json" },
      signal: undefined,
    });
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/v1/catalyst/dataset/rows?testName=Viral+Load&limit=25&offset=25",
      { headers: { Accept: "application/json" }, signal: undefined },
    );
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

  it("reads the verified publication state for one dashboard version", async () => {
    const publication = {
      status: "imported",
      dashboard: {
        id: "dashboard-1",
        versionId: "dashboard-v1",
        ordinal: 1,
        configuration: {},
        configurationDigest: "a".repeat(64),
        createdAt: "2026-08-06T00:00:00Z",
      },
      pointer: {
        bundle: { fileName: "bundle.zip", sha256: "b".repeat(64), bytes: 42 },
      },
      downloadPath: "/bundle",
      importState: {
        outcome: "imported",
        dashboardUrl: "http://localhost:18088/superset/dashboard/catalyst-dashboard-1/",
      },
    };
    fetcher.mockResolvedValue(jsonResponse(publication));

    await expect(
      api.getDashboardPublication?.("dashboard version"),
    ).resolves.toEqual(publication);
    expect(fetcher).toHaveBeenCalledWith(
      "/v1/catalyst/dashboard-builder/dashboards/dashboard%20version/publication",
      { headers: { Accept: "application/json" }, signal: undefined },
    );
  });

  it("rejects an incompatible response instead of guessing at its shape", async () => {
    fetcher.mockResolvedValue(jsonResponse({ detail: "Bad gateway" }, 502));

    await expect(api.submitQuestion(QUESTION)).rejects.toThrow("Bad gateway");
  });

  it("surfaces the Gateway nested error message", async () => {
    fetcher.mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "hub_timeout",
            message: "The local model exceeded the configured timeout.",
          },
        },
        502,
      ),
    );

    await expect(api.submitQuestion(QUESTION)).rejects.toThrow(
      "The local model exceeded the configured timeout.",
    );
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
