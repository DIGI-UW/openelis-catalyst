/**
 * Deterministic Catalyst API for the visual baseline.
 *
 * Purpose-built for the states the styling roadmap names, and deliberately
 * separate from `query-to-table.spec.ts`'s mock: that one is an acceptance
 * fixture whose job is to record request shapes, and coupling a screenshot
 * harness to it would mean every future assertion change repaints the
 * baseline. Nothing here talks to a model or a database.
 */
import type { Page } from "@playwright/test";

export const SESSION_ID = "7f2a91c4-3b5e-4d21-9a0c-1f2e3d4c5b6a";
export const SESSION_KEY = "catalyst.workbench.activeSessionId";

const V1 = "11111111-1111-4111-8111-111111111111";
const V2 = "22222222-2222-4222-8222-222222222222";
const V3 = "33333333-3333-4333-8333-333333333333";
const T1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
const T2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";
const T3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3";
const PROFILE = "catalyst-query-gemma-4-12b-qwen2.5-14b-checked";

const digest = (character: string) => character.repeat(64);

const SQL1 = `SELECT date_trunc('month', result_date) AS month,
       count(*) AS n
FROM analytics.lab_result_fact_v1
WHERE test_name = 'HIV viral load'
GROUP BY 1
ORDER BY 1`;

const SQL2 = `SELECT date_trunc('month', result_date) AS month,
       test_type,
       count(*) AS n
FROM analytics.lab_result_fact_v1
WHERE test_name = 'HIV viral load'
GROUP BY 1, 2
ORDER BY 1`;

const SQL3 = SQL2.replace("test_type,", "test_name,");

const version = (
  versionId: string,
  ordinal: number,
  sql: string,
  authorType: string,
  parentVersionId: string | null,
) => ({
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId,
  sessionId: SESSION_ID,
  parentVersionId,
  ordinal,
  authorType,
  sql,
  parameters: [],
  expectedColumns: [
    { name: "month", logicalType: "date-time", nullable: false },
    { name: "n", logicalType: "integer", nullable: false },
  ],
  queryDigest: digest(String(ordinal)),
  provenance: {
    model: authorType === "model_repair" ? "qwen2.5-14b" : "gemma-4-12b",
    collaborationRole: authorType === "model_repair" ? "reviewer" : "writer",
    profileId: PROFILE,
    catalystTraceId: `cat-trace-${ordinal}`,
    hubTraceId: `hub-trace-${ordinal}`,
  },
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: `2026-08-07T10:0${ordinal}:00Z`,
});

const cell = (value: string | number, type: string) => ({ type, value });

const okExecution = (
  ordinal: number,
  versionId: string,
  sql: string,
  ms: number,
  columns: string[],
  rows: Array<Array<{ type: string; value: string | number }>>,
) => ({
  contractVersion: "catalyst.workbench.execution.v1",
  queryDigest: digest(String(ordinal)),
  idempotencyKey: `idem-${ordinal}`,
  validationStatus: "valid",
  query: { sql, parameters: [] },
  statementTimeoutMs: 30000,
  maxRows: 1000,
  replayed: false,
  status: "succeeded",
  result: {
    columns: columns.map((name, index) => ({
      ordinal: index,
      name,
      databaseType: name === "n" ? "bigint" : "text",
      typeOid: null,
      logicalType: name === "n" ? "integer" : "string",
    })),
    rows,
    rowCount: { returned: rows.length, truncated: false, truncationReason: null },
  },
  durationMs: ms,
  executionId: `exec-${ordinal}`,
  sessionId: SESSION_ID,
  versionId,
  ordinal,
  completedAt: `2026-08-07T10:0${ordinal}:05Z`,
});

const failedExecution = {
  contractVersion: "catalyst.workbench.execution.v1",
  queryDigest: digest("2"),
  idempotencyKey: "idem-2",
  validationStatus: "valid",
  query: { sql: SQL2, parameters: [] },
  statementTimeoutMs: 30000,
  maxRows: 1000,
  replayed: false,
  status: "failed",
  databaseDiagnostic: {
    sqlstate: "42703",
    severity: "ERROR",
    message: 'column "test_type" does not exist',
    detail: null,
    hint: 'Perhaps you meant to reference the column "test_name".',
    position: 214,
  },
  durationMs: 18,
  executionId: "exec-2",
  sessionId: SESSION_ID,
  versionId: V2,
  ordinal: 2,
  completedAt: "2026-08-07T10:02:05Z",
};

const MONTH_ROWS = [
  [cell("2026-01", "string"), cell("HIV viral load", "string"), cell(118, "integer")],
  [cell("2026-02", "string"), cell("HIV viral load", "string"), cell(141, "integer")],
  [cell("2026-03", "string"), cell("HIV viral load", "string"), cell(137, "integer")],
  [cell("2026-04", "string"), cell("HIV viral load", "string"), cell(152, "integer")],
];

const validation = (versionId: string, ordinal: number) => ({
  contractVersion: "catalyst.workbench.validation.v1",
  queryDigest: digest(String(ordinal)),
  validatorRevision: "r14",
  validatorDigest: digest("d"),
  status: "warning",
  advisory: true,
  checks: [
    { name: "relation_allowlist", status: "passed", findingIds: [] },
    { name: "null_prone_column", status: "warned", findingIds: ["finding-1"] },
  ],
  findings: [
    {
      contractVersion: "catalyst.workbench.finding.v1",
      findingId: "finding-1",
      ruleCode: "NULL_PRONE_COLUMN",
      severity: "warning",
      stage: "semantic",
      message: "result_date is null in 4% of rows; months may under-count.",
      path: "$.where.result_date",
      astUnit: null,
      span: null,
      evidence: null,
      suggestedAction: "Filter on collected_date instead, or exclude nulls explicitly.",
      repairability: "model",
      validatorRevision: "r14",
    },
  ],
  durationMs: 61,
  validationId: `validation-${ordinal}`,
  sessionId: SESSION_ID,
  versionId,
  ordinal,
  createdAt: `2026-08-07T10:0${ordinal}:02Z`,
});

const profileSnapshot = {
  profileId: PROFILE,
  profileName: "Catalyst query — Gemma 4 12B writer, Qwen 2.5 14B reviewer",
  profileDigest: digest("d"),
  writer: { role: "writer", modelId: "gemma-4-12b" },
  reviewer: { role: "reviewer", modelId: "qwen2.5-14b" },
  omissions: [],
};

const turn = (
  turnId: string,
  ordinal: number,
  kind: string,
  instruction: string,
  selectedVersionId: string,
  outputs: unknown[],
) => ({
  contractVersion: "catalyst.workbench.turn.v1",
  sessionId: SESSION_ID,
  turnId,
  ordinal,
  kind,
  origin: "recorded",
  instruction,
  instructionDigest: digest("c"),
  dataSourceId: "openelis",
  profileSnapshot,
  observedBase: null,
  editorSnapshot: null,
  snapshotClassification: "not_applicable",
  unresolvedPaths: [],
  effectiveBaseVersion: null,
  manualVersion: null,
  revisionContext: null,
  hubRequestDigest: digest("e"),
  catalystTraceId: `cat-trace-${ordinal}`,
  hubTraceId: `hub-trace-${ordinal}`,
  generationEvidenceRef: {
    evidenceId: `99999999-9999-4999-8999-99999999999${ordinal}`,
    evidenceDigest: digest("f"),
    detailPath: `/v1/catalyst/workbench/sessions/${SESSION_ID}/turns/${turnId}/generation-evidence`,
  },
  recoveryReferences: null,
  status: "completed",
  outputVersions: outputs,
  selectedVersionId,
  resultingCurrentVersion: {
    versionId: selectedVersionId,
    queryDigest: digest(String(ordinal)),
  },
  events: [],
  failure: null,
  createdAt: `2026-08-07T10:0${ordinal}:00Z`,
  updatedAt: `2026-08-07T10:0${ordinal}:05Z`,
});

const output = (
  versionId: string,
  ordinal: number,
  role: string,
  selected: boolean,
  parentVersionId: string | null,
) => ({
  versionId,
  queryDigest: digest(String(ordinal)),
  parentVersionId,
  role,
  authorType: role === "reviewer" ? "model_repair" : "model",
  contractValid: true,
  validationId: null,
  selected,
});

const CATALOG = {
  contractVersion: "catalyst.workbench.editor-catalog.v1",
  catalogVersion: "analytics-catalog-v1+schema.665109d58952e881",
  schemaVersion: "665109d58952e881",
  dialect: "postgresql",
  schemas: [
    {
      name: "analytics",
      views: [
        {
          name: "lab_result_fact_v1",
          qualifiedName: "analytics.lab_result_fact_v1",
          relationType: "view",
          grain: "Exactly one row per FHIR Observation.",
          columns: [
            { name: "patient_id", logicalType: "string", databaseType: "character varying", nullable: false, description: "FHIR Patient resource identifier." },
            { name: "observation_id", logicalType: "string", databaseType: "character varying", nullable: false, description: "Observation resource identifier." },
            { name: "collected_date", logicalType: "date-time", databaseType: "timestamp with time zone", nullable: false, description: "Specimen collection instant." },
            { name: "result_date", logicalType: "date-time", databaseType: "timestamp with time zone", nullable: true, description: "Observation effective instant." },
            { name: "test_name", logicalType: "string", databaseType: "character varying", nullable: false, description: "Laboratory test name." },
            { name: "value_num", logicalType: "decimal", databaseType: "numeric", nullable: true, description: "Numeric result value." },
            { name: "result_unit", logicalType: "string", databaseType: "character varying", nullable: true, description: "Unit from result_unit." },
          ],
        },
        {
          name: "pipeline_freshness_v1",
          qualifiedName: "analytics.pipeline_freshness_v1",
          relationType: "view",
          grain: "One row per pipeline resource type.",
          columns: [
            { name: "resource_type", logicalType: "string", databaseType: "character varying", nullable: false, description: "FHIR resource type." },
            { name: "row_count", logicalType: "integer", databaseType: "bigint", nullable: false, description: "Rows currently loaded." },
          ],
        },
      ],
    },
  ],
};

const QUERY_OPTIONS = {
  contractVersion: "catalyst.query-options.v1",
  defaultProfileId: PROFILE,
  profiles: [
    {
      id: PROFILE,
      label: "Catalyst query — Gemma 4 12B writer, Qwen 2.5 14B reviewer",
      available: true,
      revisionCapable: true,
      roleModels: { query_generate: "gemma-4-12b", query_review: "qwen2.5-14b" },
    },
  ],
};

const DATA_SOURCES = {
  contractVersion: "catalyst.data-sources.v1",
  defaultDataSourceId: "openelis",
  dataSources: [{ id: "openelis", label: "OpenELIS Laboratory", available: true }],
};

const DATASET_OVERVIEW = {
  contractVersion: "catalyst.dataset-overview.v1",
  datasetId: "openelis",
  synthetic: true,
  patients: 412,
  results: 1486,
  testTypes: 9,
  firstObservedAt: "2026-01-04T00:00:00Z",
  lastObservedAt: "2026-04-28T00:00:00Z",
  tests: [
    { testName: "HIV viral load", results: 548 },
    { testName: "CD4 count", results: 331 },
  ],
  exampleQuestions: [],
};

/** A finished thread: one clean run, one failed run, one repaired run. */
const buildSession = () => {
  const versions = [
    version(V1, 1, SQL1, "model", null),
    version(V2, 2, SQL2, "model", V1),
    version(V3, 3, SQL3, "model_repair", V2),
  ];
  const executions = [
    okExecution(1, V1, SQL1, 71, ["month", "n"], MONTH_ROWS.map((row) => [row[0]!, row[2]!])),
    failedExecution,
    okExecution(3, V3, SQL3, 84, ["month", "test_name", "n"], MONTH_ROWS),
  ];
  const turns = [
    turn(T1, 1, "initial", "Monthly viral load results for 2026", V1, [
      output(V1, 1, "writer", true, null),
    ]),
    turn(T2, 2, "followup", "Split it by test type", V2, [
      output(V2, 2, "writer", true, V1),
    ]),
    turn(T3, 3, "followup", "Auto-repair of [2]", V3, [
      output(V2, 2, "writer", false, V1),
      output(V3, 3, "reviewer", true, V2),
    ]),
  ];
  const validations = [validation(V1, 1), validation(V2, 2), validation(V3, 3)];
  const currentVersion = versions.at(-1)!;

  return {
    session: {
      contractVersion: "catalyst.workbench.session.v1",
      sessionId: SESSION_ID,
      question: "Monthly viral load, 2026",
      name: "Monthly viral load, 2026",
      profileId: PROFILE,
      dataSourceId: "openelis",
      datasetId: "openelis",
      datasetVersion: "lab_result_fact_v1 · r7",
      catalogVersion: CATALOG.catalogVersion,
      currentVersionId: currentVersion.versionId,
      draftSeed: null,
      browserState: {},
      provenance: { profileId: PROFILE },
      status: "active",
      createdAt: "2026-08-07T10:00:00Z",
      updatedAt: "2026-08-07T10:05:00Z",
      versions,
      currentVersion,
      validations,
      latestValidation: validations.at(-1),
      executions,
    },
    timeline: {
      contractVersion: "catalyst.workbench.turn.timeline.v1",
      sessionId: SESSION_ID,
      currentTurnId: turns.at(-1)!.turnId,
      currentVersion: {
        versionId: currentVersion.versionId,
        queryDigest: currentVersion.queryDigest,
      },
      turns,
    },
  };
};

export interface BaselineOptions {
  /** Serve an empty session instead of the finished thread. */
  empty?: boolean;
}

export const installBaselineApi = async (
  page: Page,
  options: BaselineOptions = {},
): Promise<void> => {
  const { session, timeline } = buildSession();
  const emptySession = {
    ...session,
    question: "",
    name: "",
    currentVersionId: null,
    currentVersion: null,
    versions: [],
    validations: [],
    latestValidation: null,
    executions: [],
  };
  const emptyTimeline = {
    ...timeline,
    currentTurnId: null,
    currentVersion: null,
    turns: [],
  };

  await page.route("**/v1/catalyst/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      /^.*\/v1\/catalyst/,
      "",
    );
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });

    if (path === "/query-options") return json(QUERY_OPTIONS);
    if (path === "/data-sources") return json(DATA_SOURCES);
    if (path === "/dataset") return json(DATASET_OVERVIEW);
    if (path === "/dataset/rows")
      return json({ contractVersion: "catalyst.dataset-rows.v1", total: 0, offset: 0, limit: 25, rows: [] });
    if (path === "/workbench/catalog") return json(CATALOG);
    if (path === "/workbench/sessions")
      return json({
        contractVersion: "catalyst.workbench.session-list.v1",
        sessions: [
          {
            sessionId: SESSION_ID,
            name: options.empty ? "" : session.name,
            question: options.empty ? "" : session.question,
            dataSourceId: "openelis",
            turnCount: options.empty ? 0 : timeline.turns.length,
            createdAt: session.createdAt,
            updatedAt: session.updatedAt,
          },
        ],
      });
    if (path.endsWith("/turns"))
      return json(options.empty ? emptyTimeline : timeline);
    if (path === `/workbench/sessions/${SESSION_ID}`)
      return json(options.empty ? emptySession : session);
    if (path.startsWith("/dashboard-builder"))
      return json({
        contractVersion: "catalyst.dashboard-builder.v1",
        kind: path.split("/").pop()?.replace(/s$/, "") ?? "dataset",
        items: [],
      });
    return json({ detail: `unmocked ${path}` }, 404);
  });

  // String form: this project compiles its end-to-end sources without browser
  // typings, so a callback referencing `window` would not typecheck.
  await page.addInitScript(
    `window.localStorage.setItem(${JSON.stringify(SESSION_KEY)}, ${JSON.stringify(SESSION_ID)})`,
  );
};
