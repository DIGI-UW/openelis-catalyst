import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type {
  BoundParameter,
  WorkbenchEditorCatalog,
  WorkbenchExecution,
  WorkbenchSession,
} from "../types";
import { WorkbenchPanel } from "./WorkbenchPanel";
import { workbenchCatalogRelations } from "./workbenchPanelSupport";

const SQL =
  "SELECT patient_id, result_value FROM analytics.lab_result_fact_v1 WHERE result_value > :minimum_value";

const parameter: BoundParameter = {
  name: "minimum",
  type: "number",
  source: "question",
  value: 1000,
};

const invalidValidation: WorkbenchSession["latestValidation"] = {
  contractVersion: "catalyst.workbench.validation.v1",
  queryDigest: "sha256:query-v1",
  validatorRevision: "catalyst.workbench.validator.v1",
  validatorDigest: "sha256:validator",
  status: "invalid",
  advisory: true,
  checks: [
    {
      name: "parameter_binding",
      status: "failed",
      findingIds: ["finding-1"],
    },
  ],
  findings: [
    {
      contractVersion: "catalyst.workbench.finding.v1",
      findingId: "finding-1",
      ruleCode: "policy.parameter_name_mismatch",
      severity: "error",
      stage: "parameter_binding",
      message: "SQL placeholder :minimum_value has no matching parameter.",
      path: "$.parameters[0].name",
      astUnit: null,
      span: null,
      evidence: ":minimum_value",
      suggestedAction: "Rename the parameter to minimum_value.",
      repairability: "manual",
      validatorRevision: "catalyst.workbench.validator.v1",
    },
  ],
  durationMs: 4,
  validationId: "validation-1",
  sessionId: "session-1",
  versionId: "version-1",
  ordinal: 1,
  createdAt: "2026-07-17T12:01:00Z",
};

const version: WorkbenchSession["currentVersion"] = {
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId: "version-1",
  sessionId: "session-1",
  parentVersionId: null,
  ordinal: 1,
  authorType: "model",
  sql: SQL,
  parameters: [parameter],
  expectedColumns: [],
  queryDigest: "sha256:query-v1",
  provenance: {},
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-07-17T12:00:00Z",
};

const makeSession = (
  overrides: Partial<WorkbenchSession> = {},
): WorkbenchSession => ({
  contractVersion: "catalyst.workbench.session.v1",
  sessionId: "session-1",
  question: "How many viral load results are above 1000 copies/ml?",
  profileId: "catalyst-query-gemma-e4b",
  datasetId: "openelis-fhir",
  datasetVersion: "pipeline-run-7",
  catalogVersion: "catalog-2026.07",
  currentVersionId: "version-1",
  browserState: {},
  provenance: {
    catalystTraceId: "trace-catalyst-1",
    profileSnapshot: {
      profileLabel: "Catalyst Gemma E4B",
      roleModels: {
        query_generate: "google/gemma-3-e4b-it",
        query_review: "google/gemma-3-e4b-it",
      },
    },
    generationRawOutput: "RAW MODEL OUTPUT: missing nested parameter name",
    generationOutcome: {
      diagnosticCandidate: {
        executable: false,
        candidate: {
          status: "ready",
          sql: SQL,
          parameters: [parameter],
        },
        rawOutput: "RAW MODEL OUTPUT: missing nested parameter name",
        attempts: [
          {
            attempt: 2,
            status: "failed",
            finding_codes: ["schema.invalid"],
            findings: [
              {
                code: "schema.invalid",
                stage: "query_generate",
                severity: "error",
                path: "$.parameters[1]",
                message: "'name' is a required property",
              },
            ],
          },
        ],
      },
    },
  },
  status: "active",
  createdAt: "2026-07-17T12:00:00Z",
  updatedAt: "2026-07-17T12:01:00Z",
  versions: [version!],
  currentVersion: version,
  validations: [invalidValidation!],
  latestValidation: invalidValidation,
  executions: [],
  ...overrides,
});

const catalog: WorkbenchEditorCatalog = {
  contractVersion: "catalyst.workbench.editor-catalog.v1",
  catalogVersion: "catalog-2026.07",
  schemaVersion: "schema-v1",
  dialect: "postgresql",
  schemas: [
    {
      name: "public",
      views: [{
        name: "facilities",
        qualifiedName: "public.facilities",
        grain: "One row per facility.",
        columns: [{
          name: "id",
          logicalType: "string",
          nullable: false,
          description: "Facility identifier.",
        }],
      }],
    },
    {
      name: "analytics",
      views: [
        {
          name: "lab_result_fact_v1",
          qualifiedName: "analytics.lab_result_fact_v1",
          grain: "One row per FHIR Observation.",
          columns: [
            {
              name: "result_value",
              logicalType: "decimal",
              nullable: true,
              description: "Numeric result value.",
            },
            {
              name: "patient_id",
              logicalType: "string",
              nullable: false,
              description: "FHIR Patient resource identifier.",
            },
          ],
        },
      ],
    },
  ],
};

const defaultProps = {
  sql: SQL,
  parameters: [parameter],
  editorCatalog: catalog,
  wrapLines: true,
  onSqlChange: vi.fn(),
  onParametersChange: vi.fn(),
  onWrapLinesChange: vi.fn(),
  onClearDraft: vi.fn(),
  onRestoreCurrentVersion: vi.fn(),
  onValidate: vi.fn(),
  onRun: vi.fn(),
};

const ControlledParameterPanel = ({
  onParametersChange,
}: {
  onParametersChange: (parameters: BoundParameter[]) => void;
}) => {
  const [parameters, setParameters] = useState([parameter]);
  return (
    <WorkbenchPanel
      {...defaultProps}
      session={makeSession()}
      parameters={parameters}
      onParametersChange={(nextParameters) => {
        setParameters(nextParameters);
        onParametersChange(nextParameters);
      }}
    />
  );
};

describe("WorkbenchPanel", () => {
  it("keeps invalid SQL runnable and identifies validation as advisory", async () => {
    const user = userEvent.setup();
    const onValidate = vi.fn();
    const onRun = vi.fn();
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession()}
        onValidate={onValidate}
        onRun={onRun}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Query workbench" }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "SQL query" })).toBeVisible();
    expect(screen.getByText("Invalid", { selector: ".workbench-validation__status" })).toBeVisible();
    expect(screen.getByText(/advisory/i)).toBeVisible();
    expect(screen.getByText(/placeholder :minimum_value/i)).toBeVisible();

    const validate = screen.getByRole("button", { name: "Validate query" });
    const run = screen.getByRole("button", { name: "Run query" });
    const editor = screen.getByRole("textbox", { name: "SQL query" })
      .closest(".workbench-editor")!;
    const actions = screen.getByLabelText("Workbench actions");
    const parameters = screen.getByRole("heading", { name: "Parameters" })
      .closest(".workbench-parameters")!;
    expect(
      editor.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      actions.compareDocumentPosition(parameters) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(validate).toBeEnabled();
    expect(run).toBeEnabled();
    await user.click(validate);
    await user.click(run);
    expect(onValidate).toHaveBeenCalledOnce();
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("disables actions only while busy or when SQL is empty", () => {
    const { rerender } = render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession()}
        busy="validating"
      />,
    );
    expect(screen.getByRole("button", { name: "Validate query" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run query" })).toBeDisabled();

    rerender(
      <WorkbenchPanel {...defaultProps} session={makeSession()} sql="   " />,
    );
    expect(screen.getByRole("button", { name: "Validate query" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run query" })).toBeDisabled();
  });

  it("freezes the editor and every session mutation while a successor is generating", () => {
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession()}
        busy="generating"
      />,
    );

    expect(screen.getByRole("textbox", { name: "SQL query" })).toHaveAttribute(
      "contenteditable",
      "false",
    );
    expect(screen.getByRole("button", { name: "Wrap lines" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Format SQL" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add parameter" })).toBeDisabled();
    expect(screen.getByLabelText("Parameter 1 name")).toBeDisabled();
    expect(screen.getByLabelText("Parameter 1 type")).toBeDisabled();
    expect(screen.getByLabelText("Parameter 1 value")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Remove parameter 1" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear draft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Validate query" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run query" })).toBeDisabled();
  });

  it("offers an explicit restore action after the user clears the draft", async () => {
    const user = userEvent.setup();
    const onRestoreCurrentVersion = vi.fn();
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession()}
        sql=""
        parameters={[]}
        onRestoreCurrentVersion={onRestoreCurrentVersion}
      />,
    );

    const restore = screen.getByRole("button", { name: "Restore Query v1" });
    expect(restore).toBeEnabled();
    expect(screen.getByRole("button", { name: "Validate query" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run query" })).toBeDisabled();
    await user.click(restore);
    expect(onRestoreCurrentVersion).toHaveBeenCalledOnce();
  });

  it("keeps prior results visible and marks them stale when the editor changes", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: version!.queryDigest,
      idempotencyKey: "run-stale",
      validationStatus: "valid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "succeeded",
      result: {
        columns: [
          {
            ordinal: 0,
            name: "patient_id",
            databaseType: "text",
            typeOid: 25,
            logicalType: "string",
          },
        ],
        rows: [[{ type: "string", value: "patient-7" }]],
        rowCount: { returned: 1, truncated: false, truncationReason: null },
      },
      durationMs: 7,
      executionId: "execution-stale",
      sessionId: "session-1",
      versionId: version!.versionId,
      ordinal: 1,
      completedAt: "2026-07-17T12:02:00Z",
    };

    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
        sql={`${SQL}\nLIMIT 10`}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Results from Query v1" }),
    ).toBeVisible();
    expect(screen.getByText("Stale — editor has changes")).toBeVisible();
    expect(screen.getByText("patient-7")).toBeVisible();
  });

  it("shows the retained candidate and raw model output independently with diagnostics", () => {
    render(<WorkbenchPanel {...defaultProps} session={makeSession()} />);

    const candidate = screen.getByRole("region", { name: "Model candidate" });
    expect(within(candidate).getByText(SQL)).toBeVisible();
    const raw = screen.getByRole("region", { name: "Raw model output" });
    expect(within(raw).getByText(/RAW MODEL OUTPUT/)).toBeVisible();
    expect(screen.getByText("Attempt 2 — failed")).toBeVisible();
    expect(screen.getByText("$.parameters[1]")).toBeVisible();
    expect(screen.getByText("'name' is a required property")).toBeVisible();
  });

  it("edits typed parameters, marks model values human, and supports add/remove", async () => {
    const user = userEvent.setup();
    const onParametersChange = vi.fn();
    const { unmount } = render(
      <ControlledParameterPanel onParametersChange={onParametersChange} />,
    );

    const name = screen.getByRole("textbox", { name: "Parameter 1 name" });
    expect(name).toBeRequired();
    await user.clear(name);
    await user.type(name, "minimum_value");
    expect(onParametersChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ name: "minimum_value", source: "human" }),
    ]);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Parameter 1 type" }),
      "integer",
    );
    expect(onParametersChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ type: "integer", source: "human" }),
    ]);

    await user.click(screen.getByRole("button", { name: "Remove parameter 1" }));
    expect(onParametersChange).toHaveBeenLastCalledWith([]);
    unmount();

    render(<ControlledParameterPanel onParametersChange={onParametersChange} />);
    await user.click(screen.getByRole("button", { name: "Add parameter" }));
    expect(onParametersChange).toHaveBeenLastCalledWith([
      parameter,
      expect.objectContaining({ type: "string", source: "human" }),
    ]);
  });

  it("renders provenance and version history without a reasoning trace", () => {
    render(<WorkbenchPanel {...defaultProps} session={makeSession()} />);

    expect(screen.getByText("Catalyst Gemma E4B")).toBeVisible();
    expect(screen.getAllByText("google/gemma-3-e4b-it")).toHaveLength(2);
    expect(screen.getByText("session-1")).toBeVisible();
    expect(screen.getAllByText("version-1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("sha256:query-v1").length).toBeGreaterThan(0);
    expect(screen.getByText("Version 1")).toBeVisible();
    expect(screen.queryByText(/chain.of.thought/i)).not.toBeInTheDocument();
  });

  it("shows the profile that produced the current query lineage after switching", () => {
    const switchedVersion = {
      ...version!,
      versionId: "version-2",
      parentVersionId: version!.versionId,
      ordinal: 2,
      queryDigest: "sha256:query-v2",
      provenance: {
        profileId: "catalyst-query-split-models",
        profileLabel: "Gemma writer + Qwen reviewer",
        roleModels: {
          query_generate: "gemma-4-12b",
          query_review: "qwen2.5-14b",
        },
      },
    };
    const manualVersion = {
      ...switchedVersion,
      versionId: "version-3",
      parentVersionId: switchedVersion.versionId,
      ordinal: 3,
      authorType: "human" as const,
      queryDigest: "sha256:query-v3",
      provenance: { editedFromVersionId: switchedVersion.versionId },
    };
    const switched = makeSession({
      currentVersionId: manualVersion.versionId,
      currentVersion: manualVersion,
      versions: [version!, switchedVersion, manualVersion],
    });

    render(<WorkbenchPanel {...defaultProps} session={switched} />);

    const provenance = screen.getByRole("region", { name: "Run provenance" });
    expect(within(provenance).getByText("Gemma writer + Qwen reviewer"))
      .toBeVisible();
    expect(within(provenance).getByText("catalyst-query-split-models"))
      .toBeVisible();
    expect(within(provenance).getByText("gemma-4-12b")).toBeVisible();
    expect(within(provenance).getByText("qwen2.5-14b")).toBeVisible();
    expect(within(provenance).queryByText("Catalyst Gemma E4B"))
      .not.toBeInTheDocument();
  });

  it("shows writer and reviewer candidates, models, findings, and linked SQL versions", () => {
    const writerSql = "SELECT COUNT(*) FROM analytics.lab_results";
    const reviewerSql = "SELECT COUNT(*) AS count FROM analytics.lab_results";
    const writerVersion = {
      ...version!,
      versionId: "version-writer",
      ordinal: 1,
      sql: writerSql,
      queryDigest: "sha256:writer",
      provenance: { collaborationRole: "writer", model: "gemma-4-12b" },
    };
    const reviewerVersion = {
      ...version!,
      versionId: "version-reviewer",
      parentVersionId: writerVersion.versionId,
      ordinal: 2,
      authorType: "model_repair" as const,
      sql: reviewerSql,
      queryDigest: "sha256:reviewer",
      provenance: { collaborationRole: "reviewer", model: "qwen2.5-14b" },
    };
    const session = makeSession({
      currentVersionId: reviewerVersion.versionId,
      currentVersion: reviewerVersion,
      versions: [writerVersion, reviewerVersion],
      provenance: {
        profileSnapshot: {
          profileLabel: "Gemma writer + Qwen reviewer",
          roleModels: {
            query_generate: "gemma-4-12b",
            query_review: "qwen2.5-14b",
          },
        },
        generationOutcome: {
          modelCollaboration: {
            writer: {
              model: "gemma-4-12b",
              candidate: { sql: writerSql, parameters: [] },
              lintFindings: [
                {
                  code: "output.projection_mismatch",
                  message: "The aggregate needs the declared count alias.",
                },
              ],
            },
            reviewer: {
              model: "qwen2.5-14b",
              decision: "repair",
              candidate: { sql: reviewerSql, parameters: [] },
              checks: [{ name: "projection", status: "passed" }],
            },
            finalLintFindings: [],
          },
        },
      },
    });

    render(<WorkbenchPanel {...defaultProps} session={session} />);

    expect(screen.getByRole("heading", { name: "Writer candidate" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Reviewer correction" })).toBeVisible();
    expect(screen.getAllByText("gemma-4-12b").length).toBeGreaterThan(0);
    expect(screen.getAllByText("qwen2.5-14b").length).toBeGreaterThan(0);
    expect(screen.getByText("The aggregate needs the declared count alias.")).toBeVisible();
    expect(screen.getAllByText(writerSql).length).toBeGreaterThan(0);
    expect(screen.getAllByText(reviewerSql).length).toBeGreaterThan(0);
    expect(screen.getByText("model repair")).toBeVisible();
  });

  it("can hide initial generation evidence when notebook turns own that evidence", () => {
    const session = makeSession({
      provenance: {
        generationOutcome: {
          modelCollaboration: {
            writer: {
              model: "gemma-4-12b",
              candidate: { sql: SQL, parameters: [] },
            },
          },
        },
      },
    });

    render(
      <WorkbenchPanel
        {...defaultProps}
        session={session}
        showInitialGenerationEvidence={false}
      />,
    );

    expect(
      screen.queryByRole("heading", { name: "Generation evidence" }),
    ).not.toBeInTheDocument();
  });

  it("renders a successful dynamic execution table", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: "sha256:query-v1",
      idempotencyKey: "run-1",
      validationStatus: "invalid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "succeeded",
      result: {
        columns: [
          { ordinal: 0, name: "patient_id", databaseType: "text", typeOid: 25, logicalType: "string" },
          { ordinal: 1, name: "result_value", databaseType: "numeric", typeOid: 1700, logicalType: "decimal" },
          { ordinal: 2, name: "metadata", databaseType: "jsonb", typeOid: 3802, logicalType: "json" },
        ],
        rows: [
          [
            { type: "string", value: "patient-7" },
            { type: "decimal", value: "9000.0" },
            { type: "json", value: { source: "openelis" } },
          ],
          [
            { type: "null" },
            { type: "integer", value: 1200 },
            { type: "array", value: ["reviewed", 1] },
          ],
        ],
        rowCount: { returned: 2, truncated: true, truncationReason: "max_rows" },
      },
      durationMs: 12,
      executionId: "execution-1",
      sessionId: "session-1",
      versionId: "version-1",
      ordinal: 1,
      completedAt: "2026-07-17T12:02:00Z",
    };
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
      />,
    );

    const results = screen.getByRole("region", { name: "Latest execution" });
    expect(
      results.querySelector(".workbench-execution__table-wrap--bounded"),
    ).toBeInTheDocument();
    expect(within(results).getByRole("columnheader", { name: "patient_id" })).toBeVisible();
    expect(within(results).getByRole("columnheader", { name: "result_value" })).toBeVisible();
    expect(within(results).getByText("patient-7")).toBeVisible();
    expect(within(results).getByText("9000.0")).toBeVisible();
    expect(within(results).getByText('{"source":"openelis"}')).toBeVisible();
    expect(within(results).getByText('["reviewed",1]')).toBeVisible();
    expect(within(results).getByLabelText("No value")).toBeVisible();
    expect(within(results).getByText(/truncated.*max_rows/i)).toBeVisible();
  });

  it("derives nonblocking blank-value feedback for a retained legacy execution", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: "sha256:query-v1",
      idempotencyKey: "run-blank",
      validationStatus: "valid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "succeeded",
      result: {
        columns: [
          { ordinal: 0, name: "name_display", databaseType: "text", typeOid: 25, logicalType: "string" },
        ],
        rows: [[{ type: "null" }], [{ type: "string", value: "" }]],
        rowCount: { returned: 2, truncated: false, truncationReason: null },
      },
      durationMs: 7,
      executionId: "execution-blank",
      sessionId: "session-1",
      versionId: "version-1",
      ordinal: 1,
      completedAt: "2026-07-17T12:02:00Z",
    };
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
      />,
    );

    const results = screen.getByRole("region", { name: "Latest execution" });
    expect(within(results).getByText("Returned values need review")).toBeVisible();
    expect(
      within(results).getByText(/`name_display` was blank or NULL in all 2 returned rows/i),
    ).toBeVisible();
    expect(within(results).getByLabelText("No value")).toBeVisible();
    expect(within(results).getByLabelText("Empty string")).toBeVisible();
    expect(within(results).getByRole("table")).toBeVisible();
    expect(within(results).getByText("Succeeded")).toBeVisible();
  });

  it("keeps backend result warnings authoritative", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: "sha256:query-v1",
      idempotencyKey: "run-authoritative-warning",
      validationStatus: "valid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "succeeded",
      result: {
        columns: [
          { ordinal: 0, name: "name_display", databaseType: "text", typeOid: 25, logicalType: "string" },
        ],
        rows: [[{ type: "null" }]],
        rowCount: { returned: 1, truncated: false, truncationReason: null },
        warnings: ["Authoritative execution warning."],
      },
      durationMs: 7,
      executionId: "execution-authoritative-warning",
      sessionId: "session-1",
      versionId: "version-1",
      ordinal: 1,
      completedAt: "2026-07-17T12:02:00Z",
    };
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
      />,
    );

    const results = screen.getByRole("region", { name: "Latest execution" });
    expect(within(results).getByText("Authoritative execution warning.")).toBeVisible();
    expect(within(results).queryByText(/name_display.*blank or NULL/i)).not.toBeInTheDocument();
  });

  it("distinguishes a successful zero-row result from blank returned values", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: "sha256:query-v1",
      idempotencyKey: "run-empty",
      validationStatus: "valid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "succeeded",
      result: {
        columns: [
          { ordinal: 0, name: "name_display", databaseType: "text", typeOid: 25, logicalType: "string" },
        ],
        rows: [],
        rowCount: { returned: 0, truncated: false, truncationReason: null },
      },
      durationMs: 6,
      executionId: "execution-empty",
      sessionId: "session-1",
      versionId: "version-1",
      ordinal: 1,
      completedAt: "2026-07-17T12:02:00Z",
    };
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
      />,
    );

    const results = screen.getByRole("region", { name: "Latest execution" });
    expect(
      within(results).getByText(/ran successfully but matched no rows/i),
    ).toBeVisible();
    expect(within(results).queryByText("Returned values need review")).not.toBeInTheDocument();
    expect(within(results).queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders a failed database diagnostic with actionable fields", () => {
    const execution: WorkbenchExecution = {
      contractVersion: "catalyst.workbench.execution.v1",
      queryDigest: "sha256:query-v1",
      idempotencyKey: "run-2",
      validationStatus: "invalid",
      query: { sql: SQL, parameters: [parameter] },
      statementTimeoutMs: 3000,
      maxRows: 100,
      replayed: false,
      status: "failed",
      databaseDiagnostic: {
        sqlstate: "42P01",
        severity: "ERROR",
        message: "relation does not exist",
        detail: "Missing analytics.bad_view",
        hint: "Use analytics.lab_result_fact_v1",
        position: 15,
      },
      durationMs: 3,
      executionId: "execution-2",
      sessionId: "session-1",
      versionId: "version-1",
      ordinal: 2,
      completedAt: "2026-07-17T12:03:00Z",
    };
    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession({ executions: [execution] })}
      />,
    );

    const diagnostic = screen.getByRole("alert");
    expect(within(diagnostic).getByText("42P01")).toBeVisible();
    expect(within(diagnostic).getByText("relation does not exist")).toBeVisible();
    expect(within(diagnostic).getByText("Missing analytics.bad_view")).toBeVisible();
    expect(within(diagnostic).getByText("Use analytics.lab_result_fact_v1")).toBeVisible();
    expect(within(diagnostic).getByText("15")).toBeVisible();
  });

  it("normalizes editor catalog relations deterministically and treats load failure as nonblocking", () => {
    expect(workbenchCatalogRelations(catalog)).toEqual([
      {
        schema: "analytics",
        name: "lab_result_fact_v1",
        columns: ["patient_id", "result_value"],
      },
      { schema: "public", name: "facilities", columns: ["id"] },
    ]);
    expect(
      workbenchCatalogRelations({
        ...catalog,
        schemas: [...catalog.schemas].reverse(),
      }),
    ).toEqual(workbenchCatalogRelations(catalog));

    render(
      <WorkbenchPanel
        {...defaultProps}
        session={makeSession()}
        editorCatalog={null}
        catalogLoadingFailed
      />,
    );
    expect(screen.getByText(/catalog completion is unavailable/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Run query" })).toBeEnabled();
  });
});
