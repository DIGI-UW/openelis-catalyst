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
  source: "question" | "human";
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
  reasoningTrace?: ReasoningTrace;
  createdAt: string;
  state: "awaiting_acceptance";
}

export interface ValidationCheck {
  name: string;
  status: "passed" | "warned" | "failed";
  message?: string;
}

export interface ReasoningTrace {
  traceId: string;
  profileId: string;
  status: "passed" | "warned" | "rejected";
  stages: string[];
  roleModels: Record<string, string>;
  checks: ValidationCheck[];
}

export interface QueryProvenance {
  profileId: string;
  traceId: string;
  contextSourceIds: string[];
}

export interface GeneratedQueryCandidate {
  status: "ready" | "needs_clarification" | "unsupported" | "rejected";
  target?: QueryTarget;
  sql?: string;
  parameters?: BoundParameter[];
  expectedColumns?: Column[];
  clarification?: string;
  message?: string;
}

export interface DiagnosticFinding {
  code: string;
  stage: string;
  severity: "warning" | "error";
  path: string;
  message: string;
  evidence?: string;
  suggestedAction?: string;
}

export interface DiagnosticCandidate {
  executable: false;
  candidate?: GeneratedQueryCandidate;
  rawOutput?: string;
  attempts?: Array<{
    attempt: number;
    status: "passed" | "failed";
    finding_codes: string[];
    findings: DiagnosticFinding[];
  }>;
}

export interface CatalystQueryOutcome {
  contractVersion: "catalyst.query.v1";
  deploymentMode: DeploymentMode;
  status: "needs_clarification" | "unsupported" | "rejected";
  question: string;
  clarification?: string;
  message?: string;
  diagnosticCandidate?: DiagnosticCandidate;
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

export type JsonValue =
  | null
  | string
  | number
  | boolean
  | JsonValue[]
  | { [key: string]: JsonValue };

export type TaggedCell =
  | { type: "null" }
  | { type: "string"; value: string }
  | { type: "integer"; value: number }
  | { type: "decimal"; value: string }
  | { type: "boolean"; value: boolean }
  | { type: "date"; value: string }
  | { type: "date-time"; value: string }
  | { type: "time"; value: string }
  | { type: "json"; value: JsonValue }
  | { type: "array"; value: JsonValue[] }
  | { type: "binary"; value: string }
  | { type: "interval"; value: string };

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
    profileId: string;
  };
  reasoningTrace?: ReasoningTrace;
  warnings: string[];
}

export interface QueryProfile {
  id: string;
  label: string;
  available: boolean;
  requiredModels: string[];
  roleModels: Record<string, string>;
  stages: string[];
  unavailableReasons: string[];
}

export interface QueryOptions {
  contractVersion: "catalyst.query-options.v1";
  defaultProfileId: string;
  profiles: QueryProfile[];
}

export interface DatasetTestSummary {
  testName: string;
  unit: string | null;
  results: number;
  patients: number;
  minimum: string | null;
  median: string | null;
  maximum: string | null;
}

export interface DatasetOverview {
  contractVersion: "catalyst.dataset-overview.v1";
  datasetId: string | null;
  dataSource?: string | null;
  pipelineRunId?: string | null;
  synthetic: boolean | null;
  patients: number;
  results: number;
  testTypes: number;
  firstObservedAt: string | null;
  lastObservedAt: string | null;
  tests: DatasetTestSummary[];
  exampleQuestions: string[];
}

export interface DatasetRow {
  observationId: string;
  patientId: string;
  testName: string;
  value: string | null;
  unit: string | null;
  observedAt: string | null;
  issuedAt: string | null;
  turnaroundMinutes: string | null;
}

export interface DatasetRows {
  contractVersion: "catalyst.dataset-rows.v1";
  total: number;
  limit: number;
  offset: number;
  rows: DatasetRow[];
}

export interface WorkbenchEditorCatalogColumn {
  name: string;
  logicalType: string;
}

export interface WorkbenchEditorCatalogView {
  name: string;
  columns: WorkbenchEditorCatalogColumn[];
}

export interface WorkbenchEditorCatalogSchema {
  name: string;
  views: WorkbenchEditorCatalogView[];
}

export interface WorkbenchEditorCatalog {
  contractVersion: "catalyst.workbench.editor-catalog.v1";
  catalogVersion: string;
  schemaVersion: string;
  dialect: "postgresql";
  schemas: WorkbenchEditorCatalogSchema[];
}

export interface WorkbenchFinding {
  contractVersion: "catalyst.workbench.finding.v1";
  findingId: string;
  ruleCode: string;
  severity: "error" | "warning" | "info";
  stage: string;
  message: string;
  path: string;
  astUnit: unknown;
  span: unknown;
  evidence: unknown;
  suggestedAction: string | null;
  repairability: "none" | "manual" | "deterministic" | "model";
  validatorRevision: string;
}

export interface WorkbenchValidation {
  contractVersion: "catalyst.workbench.validation.v1";
  queryDigest: string;
  validatorRevision: string;
  validatorDigest: string;
  status: "invalid" | "warning" | "valid";
  advisory: true;
  checks: Array<{
    name: string;
    status: "passed" | "warned" | "failed";
    findingIds: string[];
  }>;
  findings: WorkbenchFinding[];
  durationMs: number;
  validationId: string;
  sessionId: string;
  versionId: string;
  ordinal: number;
  createdAt: string;
}

export interface WorkbenchQueryVersion {
  contractVersion: "catalyst.workbench.query-version.v1";
  versionId: string;
  sessionId: string;
  parentVersionId: string | null;
  ordinal: number;
  authorType: "model" | "human" | "deterministic_repair" | "model_repair";
  sql: string;
  parameters: BoundParameter[];
  expectedColumns: Column[];
  queryDigest: string;
  provenance: Record<string, unknown>;
  sourceFindingIds: string[];
  repairProposalId: string | null;
  createdAt: string;
}

export interface WorkbenchDatabaseDiagnostic {
  sqlstate: string | null;
  severity: string | null;
  message: string;
  detail: string | null;
  hint: string | null;
  position: number | null;
}

export interface WorkbenchExecution {
  contractVersion: "catalyst.workbench.execution.v1";
  queryDigest: string;
  idempotencyKey: string;
  validationStatus: "not_run" | "invalid" | "warning" | "valid";
  query: {
    sql: string;
    parameters: BoundParameter[];
  };
  statementTimeoutMs: number;
  maxRows: number;
  replayed: boolean;
  status: "succeeded" | "failed";
  result?: {
    columns: Array<{
      ordinal: number;
      name: string;
      databaseType: string;
      typeOid: number | null;
      logicalType: string;
    }>;
    rows: TaggedCell[][];
    rowCount: {
      returned: number;
      truncated: boolean;
      truncationReason: string | null;
    };
  };
  databaseDiagnostic?: WorkbenchDatabaseDiagnostic;
  durationMs: number;
  executionId: string;
  sessionId: string;
  versionId: string;
  ordinal: number;
  completedAt: string;
}

export interface WorkbenchSession {
  contractVersion: "catalyst.workbench.session.v1";
  sessionId: string;
  question: string;
  profileId: string;
  datasetId: string;
  datasetVersion: string;
  catalogVersion: string;
  currentVersionId: string | null;
  browserState: Record<string, unknown>;
  provenance: Record<string, unknown>;
  status: string;
  createdAt: string;
  updatedAt: string;
  versions: WorkbenchQueryVersion[];
  currentVersion: WorkbenchQueryVersion | null;
  validations: WorkbenchValidation[];
  latestValidation: WorkbenchValidation | null;
  executions: WorkbenchExecution[];
}

export interface WorkbenchVersionDraft {
  parentVersionId?: string;
  parentQueryDigest?: string;
  sql: string;
  parameters: BoundParameter[];
  expectedColumns?: Column[];
}

export interface CatalystExecutionOutcome {
  contractVersion: "catalyst.execution.outcome.v1";
  deploymentMode: DeploymentMode;
  previewId: string;
  idempotencyKey: string;
  status: "in_progress" | "not_found" | "conflict" | "failed";
  errorCode:
    | "execution_in_progress"
    | "execution_not_found"
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
