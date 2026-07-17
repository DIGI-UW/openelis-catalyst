import type {
  CatalystExecutionOutcome,
  CatalystPolicyOutcome,
  CatalystPreview,
  CatalystQueryOutcome,
  CatalystTable,
  TaggedCell,
} from "../types";

export const QUESTION = "Show recent viral load results";

export const preview: CatalystPreview = {
  contractVersion: "catalyst.preview.v1",
  deploymentMode: "demo",
  previewId: "preview-123",
  queryDigest: "sha256:preview-digest",
  question: QUESTION,
  target: {
    dataSource: "openelis-analytics",
    catalogVersion: "2026.07",
    dialect: "postgresql",
    approvedViews: ["analytics.vw_viral_load_results"],
  },
  sql: [
    "SELECT collected_on, result_value",
    "FROM analytics.vw_viral_load_results",
    "WHERE result_value >= :minimum_result",
  ].join("\n"),
  parameters: [
    {
      name: "minimum_result",
      type: "integer",
      source: "question",
      value: 80,
    },
  ],
  expectedColumns: [
    { name: "collected_on", logicalType: "date", nullable: false },
    {
      name: "result_value",
      logicalType: "integer",
      nullable: false,
      unit: "copies/mL",
    },
  ],
  reasoningTrace: {
    traceId: "hub-trace-456",
    profileId: "catalyst-query-gemma-e4b",
    status: "passed",
    stages: ["context", "query_generate", "query_review", "query_finalize"],
    roleModels: {
      query_generate: "google/gemma-4-e4b",
      query_review: "google/gemma-4-e4b",
    },
    checks: [{ name: "review", status: "passed", message: "Candidate approved." }],
  },
  createdAt: "2026-07-16T00:00:00Z",
  expiresAt: "2026-07-16T00:05:00Z",
  state: "awaiting_acceptance",
};

export const table: CatalystTable = {
  contractVersion: "catalyst.table.v1",
  deploymentMode: "demo",
  question: QUESTION,
  preview: {
    previewId: preview.previewId,
    queryDigest: preview.queryDigest,
    acceptedAt: "2026-07-16T00:01:00Z",
  },
  query: {
    sql: preview.sql,
    parameters: preview.parameters,
  },
  table: {
    columns: preview.expectedColumns,
    rows: [
      [
        { type: "date", value: "2026-07-01" },
        { type: "integer", value: 1200 },
      ],
      [
        { type: "date", value: "2026-06-01" },
        { type: "integer", value: 450 },
      ],
      [
        { type: "date", value: "2026-05-01" },
        { type: "integer", value: 80 },
      ],
    ],
    rowCount: {
      returned: 3,
      total: 3,
      totalIsExact: true,
      truncated: false,
      limit: 100,
    },
  },
  source: {
    dataSource: "openelis-analytics",
    catalogVersion: "2026.07",
    views: ["analytics.vw_viral_load_results"],
    freshness: {
      sourceWatermark: "2026-07-15T23:55:00Z",
      pipelineRunId: "pipeline-run-77",
      completionState: "complete",
      observedLagSeconds: 300,
    },
  },
  execution: {
    status: "succeeded",
    durationMs: 42,
    statementTimeoutMs: 5000,
  },
  provenance: {
    catalystTraceId: "cat-trace-123",
    hubTraceId: "hub-trace-456",
    profileId: "catalyst-query-gemma-e4b",
  },
  reasoningTrace: preview.reasoningTrace,
  warnings: [],
};

export const queryOutcome = (
  status: "needs_clarification" | "unsupported" | "rejected",
): CatalystQueryOutcome => ({
  contractVersion: "catalyst.query.v1",
  deploymentMode: "demo",
  status,
  question: QUESTION,
  ...(status === "needs_clarification"
    ? { clarification: "Which facility should be included?" }
    : { message: `The question was ${status}.` }),
  validation: {
    status: status === "needs_clarification" ? "warned" : "rejected",
    checks: [],
  },
  provenance: {
    profileId: "catalyst-query-gemma-e4b",
    traceId: "hub-trace-456",
    contextSourceIds: ["catalog:2026.07"],
  },
});

export const policyOutcome: CatalystPolicyOutcome = {
  contractVersion: "catalyst.policy.outcome.v1",
  deploymentMode: "demo",
  status: "rejected",
  errorCode: "query_policy_rejected",
  message: "Catalyst policy rejected the proposed query.",
  violations: [
    {
      code: "unapproved_view",
      message: "The query references a view outside the approved catalog.",
    },
  ],
  catalystTraceId: "cat-trace-policy",
};

export const executionOutcome = (
  status: CatalystExecutionOutcome["status"],
): CatalystExecutionOutcome => {
  const details = {
    in_progress: {
      errorCode: "execution_in_progress" as const,
      retryable: true,
    },
    not_found: {
      errorCode: "execution_not_found" as const,
      retryable: false,
    },
    expired: {
      errorCode: "preview_expired" as const,
      retryable: false,
    },
    conflict: {
      errorCode: "idempotency_conflict" as const,
      retryable: false,
    },
    failed: {
      errorCode: "execution_failed" as const,
      retryable: false,
    },
  }[status];

  return {
    contractVersion: "catalyst.execution.outcome.v1",
    deploymentMode: "demo",
    previewId: preview.previewId,
    idempotencyKey: "idempotency-123",
    status,
    ...details,
    message: `Execution ${status.replace("_", " ")}.`,
    replayed: false,
  };
};

export const allTaggedCells: TaggedCell[] = [
  { type: "string", value: "positive" },
  { type: "integer", value: 1200 },
  { type: "decimal", value: "0.1250" },
  { type: "boolean", value: true },
  { type: "date", value: "2026-07-16" },
  { type: "date-time", value: "2026-07-16T00:00:00Z" },
  { type: "null" },
];
