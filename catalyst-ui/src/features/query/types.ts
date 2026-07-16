export type DeploymentMode = "demo";

export type ParameterType =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "date"
  | "date-time"
  | "string-list"
  | "integer-list";

export type LogicalType =
  | "string"
  | "integer"
  | "decimal"
  | "boolean"
  | "date"
  | "date-time";

export interface BoundParameter {
  name: string;
  type: ParameterType;
  source: "question";
  value: unknown;
}

export interface Column {
  name: string;
  logicalType: LogicalType;
  nullable: boolean;
  unit?: string;
}

export interface QueryTarget {
  dataSource: string;
  catalogVersion: string;
  dialect: string;
  approvedViews: string[];
}

export interface CatalystPreview {
  contractVersion: "catalyst.preview.v1";
  deploymentMode: DeploymentMode;
  previewId: string;
  queryDigest: string;
  question: string;
  target: QueryTarget;
  sql: string;
  parameters: BoundParameter[];
  expectedColumns: Column[];
  createdAt: string;
  expiresAt: string;
  state: "awaiting_acceptance";
}

export interface ValidationCheck {
  name: string;
  status: "passed" | "warned" | "failed";
  message?: string;
}

export interface QueryProvenance {
  profileId: "catalyst-query-checked";
  traceId: string;
  contextSourceIds: string[];
}

export interface CatalystQueryOutcome {
  contractVersion: "catalyst.query.v1";
  deploymentMode: DeploymentMode;
  status: "needs_clarification" | "unsupported" | "rejected";
  question: string;
  clarification?: string;
  message?: string;
  validation: {
    status: "warned" | "rejected";
    checks: ValidationCheck[];
  };
  provenance: QueryProvenance;
}

export interface CatalystPolicyOutcome {
  contractVersion: "catalyst.policy.outcome.v1";
  deploymentMode: DeploymentMode;
  status: "rejected";
  errorCode: "query_policy_rejected";
  message: string;
  violations: Array<{
    code: string;
    message: string;
  }>;
  catalystTraceId: string;
}

export type TaggedCell =
  | { type: "null" }
  | { type: "string"; value: string }
  | { type: "integer"; value: number }
  | { type: "decimal"; value: string }
  | { type: "boolean"; value: boolean }
  | { type: "date"; value: string }
  | { type: "date-time"; value: string };

export interface CatalystTable {
  contractVersion: "catalyst.table.v1";
  deploymentMode: DeploymentMode;
  question: string;
  preview: {
    previewId: string;
    queryDigest: string;
    acceptedAt: string;
  };
  query: {
    sql: string;
    parameters: BoundParameter[];
  };
  table: {
    columns: Column[];
    rows: TaggedCell[][];
    rowCount: {
      returned: number;
      total: number | null;
      totalIsExact: boolean;
      truncated: boolean;
      limit: number;
    };
  };
  source: {
    dataSource: string;
    catalogVersion: string;
    views: string[];
    freshness: {
      sourceWatermark: string;
      pipelineRunId: string;
      completionState: "complete" | "partial";
      observedLagSeconds: number;
    };
  };
  execution: {
    status: "succeeded";
    durationMs: number;
    statementTimeoutMs: number;
  };
  provenance: {
    catalystTraceId: string;
    hubTraceId: string;
    profileId: "catalyst-query-checked";
  };
  warnings: string[];
}

export interface CatalystExecutionOutcome {
  contractVersion: "catalyst.execution.outcome.v1";
  deploymentMode: DeploymentMode;
  previewId: string;
  idempotencyKey: string;
  status: "in_progress" | "not_found" | "expired" | "conflict" | "failed";
  errorCode:
    | "execution_in_progress"
    | "execution_not_found"
    | "preview_expired"
    | "preview_consumed"
    | "idempotency_conflict"
    | "execution_failed";
  message: string;
  replayed: boolean;
  retryable: boolean;
}

export type CatalystSubmission =
  | CatalystPreview
  | CatalystQueryOutcome
  | CatalystPolicyOutcome;

export type CatalystExecutionResponse =
  | CatalystTable
  | CatalystExecutionOutcome;

export const isPreview = (
  response: CatalystSubmission,
): response is CatalystPreview =>
  response.contractVersion === "catalyst.preview.v1";

export const isTable = (
  response: CatalystExecutionResponse,
): response is CatalystTable =>
  response.contractVersion === "catalyst.table.v1";
