import { Button, InlineNotification, Tag } from "@carbon/react";
import { useState } from "react";
import type {
  BoundParameter,
  ParameterType,
  TaggedCell,
  WorkbenchEditorCatalog,
  WorkbenchExecution,
  WorkbenchSession,
} from "../types";
import { editorContentMatchesVersion } from "../editorDigest";
import { SqlEditor } from "./SqlEditor";
import { workbenchCatalogRelations } from "./workbenchPanelSupport";
import "./WorkbenchPanel.css";

const PARAMETER_TYPES: readonly ParameterType[] = [
  "string",
  "integer",
  "number",
  "boolean",
  "date",
  "date-time",
  "string-list",
  "integer-list",
];

interface WorkbenchPanelProps {
  session: WorkbenchSession;
  sql: string;
  parameters: BoundParameter[];
  editorCatalog?: WorkbenchEditorCatalog | null;
  catalogLoadingFailed?: boolean;
  wrapLines: boolean;
  busy?: "generating" | "validating" | "running" | null;
  error?: string | null;
  announcement?: string;
  /** Outcome of the last check, reported where the button that ran it is. */
  checkOutcome?: {
    status: "invalid" | "warning" | "valid";
    findings: number;
  } | null;
  onOpenValidationDetails?: () => void;
  sqlEditorFocusRequestId?: number;
  showExecutionResult?: boolean;
  onSqlChange: (sql: string) => void;
  onParametersChange: (parameters: BoundParameter[]) => void;
  onWrapLinesChange: (wrapLines: boolean) => void;
  onClearDraft: () => void;
  onRestoreCurrentVersion: () => void;
  onValidate: () => void;
  onRun: () => void;
}

const displayParameterValue = (value: unknown) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const parseParameterValue = (source: string, type: ParameterType): unknown => {
  if (type === "boolean") {
    if (source === "true") return true;
    if (source === "false") return false;
    return source;
  }
  if (type === "integer") {
    const value = Number(source);
    return source.trim() && Number.isInteger(value) ? value : source;
  }
  if (type === "number") {
    const value = Number(source);
    return source.trim() && Number.isFinite(value) ? value : source;
  }
  if (type === "string-list") {
    try {
      const value: unknown = JSON.parse(source);
      return Array.isArray(value) && value.every((item) => typeof item === "string")
        ? value
        : source;
    } catch {
      return source;
    }
  }
  if (type === "integer-list") {
    try {
      const value: unknown = JSON.parse(source);
      return Array.isArray(value) && value.every(Number.isInteger) ? value : source;
    } catch {
      return source;
    }
  }
  return source;
};

const renderTaggedCell = (cell: TaggedCell | undefined) => {
  if (!cell || cell.type === "null") {
    return <span aria-label="No value">—</span>;
  }
  if (cell.type === "string" && !cell.value.trim()) {
    return (
      <span className="workbench-execution__blank-cell" aria-label="Empty string">
        Empty string
      </span>
    );
  }
  if (cell.type === "json" || cell.type === "array") {
    return JSON.stringify(cell.value);
  }
  return String(cell.value);
};

const executionResultWarnings = (
  result: NonNullable<WorkbenchExecution["result"]>,
) => {
  if (result.warnings !== undefined) return result.warnings;
  if (result.rows.length === 0 || result.columns.length === 0) return [];

  const blankColumns = result.columns
    .filter((_, columnIndex) =>
      result.rows.every((row) => {
        const cell = row[columnIndex];
        return (
          cell?.type === "null" ||
          (cell?.type === "string" && !cell.value.trim())
        );
      }),
    )
    .map((column) => column.name);
  if (blankColumns.length === 0) return [];

  const displayedNames = blankColumns
    .slice(0, 8)
    .map((name) => `\`${name}\``)
    .join(", ");
  const omitted = blankColumns.length - 8;
  const names = omitted > 0 ? `${displayedNames}, and ${omitted} more` : displayedNames;
  const rowLabel = result.rows.length === 1 ? "row" : "rows";
  const scope = result.rowCount.truncated ? "displayed" : "returned";
  const verb = blankColumns.length === 1 ? "was" : "were";
  const truncatedNote = result.rowCount.truncated
    ? " This check covers displayed rows only because results were truncated."
    : "";
  return [
    `${names} ${verb} blank or NULL in all ${result.rows.length} ${scope} ${rowLabel}. ` +
      `Select a populated column or revise the SQL expression.${truncatedNote}`,
  ];
};

interface ParameterEditorProps {
  parameters: BoundParameter[];
  disabled?: boolean;
  onChange: (parameters: BoundParameter[]) => void;
}

const ParameterEditor = ({
  parameters,
  disabled = false,
  onChange,
}: ParameterEditorProps) => {
  const updateParameter = (
    index: number,
    update: Partial<BoundParameter>,
  ) => {
    onChange(
      parameters.map((parameter, parameterIndex) =>
        parameterIndex === index
          ? { ...parameter, ...update, source: "human" }
          : parameter,
      ),
    );
  };

  const addParameter = () => {
    onChange([
      ...parameters,
      {
        name: `parameter_${parameters.length + 1}`,
        type: "string",
        source: "human",
        value: "",
      },
    ]);
  };

  const removeParameter = (index: number) => {
    onChange(parameters.filter((_, parameterIndex) => parameterIndex !== index));
  };

  return (
    <section className="workbench-parameters" aria-labelledby="parameters-title">
      <div className="workbench-subheading workbench-subheading--row">
        <div>
          <h3 id="parameters-title">Parameters</h3>
          {/* The explanation only matters once there is one to name. */}
          {parameters.length > 0 && (
            <p>Names must match the SQL placeholders exactly.</p>
          )}
        </div>
        <Button
          type="button"
          kind="ghost"
          size="sm"
          disabled={disabled}
          onClick={addParameter}
        >
          Add parameter
        </Button>
      </div>
      {parameters.length === 0 ? (
        <p className="workbench-empty-note">None.</p>
      ) : (
        <div className="workbench-parameters__list">
          {parameters.map((parameter, index) => {
            const ordinal = index + 1;
            return (
              <fieldset
                className="workbench-parameter"
                disabled={disabled}
                key={`parameter-${index}`}
              >
                <legend>Parameter {ordinal}</legend>
                <label>
                  <span>Parameter {ordinal} name</span>
                  <input
                    required
                    value={parameter.name}
                    onChange={(event) =>
                      updateParameter(index, { name: event.target.value })
                    }
                    aria-label={`Parameter ${ordinal} name`}
                  />
                </label>
                <label>
                  <span>Parameter {ordinal} type</span>
                  <select
                    value={parameter.type}
                    onChange={(event) =>
                      updateParameter(index, {
                        type: event.target.value as ParameterType,
                      })
                    }
                    aria-label={`Parameter ${ordinal} type`}
                  >
                    {PARAMETER_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Parameter {ordinal} value</span>
                  <input
                    value={displayParameterValue(parameter.value)}
                    onChange={(event) =>
                      updateParameter(index, {
                        value: parseParameterValue(event.target.value, parameter.type),
                      })
                    }
                    aria-label={`Parameter ${ordinal} value`}
                  />
                </label>
                <div className="workbench-parameter__meta">
                  <span>Source: {parameter.source}</span>
                  <Button
                    type="button"
                    kind="danger--ghost"
                    size="sm"
                    onClick={() => removeParameter(index)}
                  >
                    Remove parameter {ordinal}
                  </Button>
                </div>
              </fieldset>
            );
          })}
        </div>
      )}
    </section>
  );
};

const latestExecution = (executions: WorkbenchExecution[]) =>
  executions.reduce<WorkbenchExecution | null>(
    (latest, execution) =>
      latest === null || execution.ordinal > latest.ordinal ? execution : latest,
    null,
  );

export const ExecutionResult = ({
  session,
  sql,
  parameters,
  executionOverride,
  immutableSnapshot = false,
  compact = false,
  pageSize,
}: {
  session: WorkbenchSession;
  sql: string;
  parameters: BoundParameter[];
  executionOverride?: WorkbenchExecution;
  immutableSnapshot?: boolean;
  /**
   * Drop the section heading and status tags. A notebook cell already names
   * its query version and reports the run outcome in its own header, so
   * repeating them inside the result is noise.
   */
  compact?: boolean;
  pageSize?: number;
}) => {
  const execution = executionOverride ?? latestExecution(session.executions);
  const [pageState, setPageState] = useState({ executionId: "", page: 0 });
  if (!execution) return null;
  const page =
    pageState.executionId === execution.executionId ? pageState.page : 0;
  const executionVersion = session.versions.find(
    (version) => version.versionId === execution.versionId,
  ) ?? null;
  const queryLabel = executionVersion
    ? `Query v${executionVersion.ordinal}`
    : "query version unavailable";
  // An immutable snapshot is a recorded run shown beside the version that
  // produced it, so it is never stale and the comparison is not just unused
  // but meaningless — the editor may hold something entirely different.
  const resultIsStale =
    !immutableSnapshot &&
    (session.currentVersionId !== execution.versionId ||
      !(executionVersion
        ? editorContentMatchesVersion(
            {
              sql,
              parameters,
              expectedColumns: executionVersion.expectedColumns,
            },
            executionVersion,
          )
        : sql === execution.query.sql &&
          JSON.stringify(parameters) ===
            JSON.stringify(execution.query.parameters)));

  if (execution.status === "failed") {
    const diagnostic = execution.databaseDiagnostic;
    return (
      <section
        className="workbench-execution"
        data-compact={compact ? "true" : undefined}
        aria-label="Latest execution"
      >
        {!compact && (
          <div className="workbench-subheading workbench-subheading--row">
            <div>
              <h3>Execution failed for {queryLabel}</h3>
              <p>Execution {execution.ordinal}</p>
            </div>
            <Tag type="red">Failed</Tag>
          </div>
        )}
        <div className="workbench-database-error" role="alert">
          <h4>{diagnostic?.message ?? "Database execution failed"}</h4>
          <dl>
            {diagnostic?.sqlstate && (
              <div>
                <dt>SQLSTATE</dt>
                <dd>{diagnostic.sqlstate}</dd>
              </div>
            )}
            {diagnostic?.severity && (
              <div>
                <dt>Severity</dt>
                <dd>{diagnostic.severity}</dd>
              </div>
            )}
            {diagnostic?.detail && (
              <div>
                <dt>Detail</dt>
                <dd>{diagnostic.detail}</dd>
              </div>
            )}
            {diagnostic?.hint && (
              <div>
                <dt>Hint</dt>
                <dd>{diagnostic.hint}</dd>
              </div>
            )}
            {diagnostic?.position !== null && diagnostic?.position !== undefined && (
              <div>
                <dt>Position</dt>
                <dd>{diagnostic.position}</dd>
              </div>
            )}
          </dl>
        </div>
      </section>
    );
  }

  const result = execution.result;
  if (!result) {
    return (
      <section
        className="workbench-execution"
        data-compact={compact ? "true" : undefined}
        aria-label="Latest execution"
      >
        <h3>Results from {queryLabel}</h3>
        <p>The database reported success without a tabular result.</p>
      </section>
    );
  }
  const columnOrder = result.columns
    .map((_, index) => index)
    .sort((left, right) => result.columns[left]!.ordinal - result.columns[right]!.ordinal);
  const resultWarnings = executionResultWarnings(result);
  const boundedPageSize = pageSize && pageSize > 0 ? pageSize : result.rows.length;
  const pageCount = Math.max(1, Math.ceil(result.rows.length / Math.max(1, boundedPageSize)));
  const safePage = Math.min(page, pageCount - 1);
  const firstVisibleRow = safePage * boundedPageSize;
  const visibleRows = result.rows.slice(firstVisibleRow, firstVisibleRow + boundedPageSize);

  return (
    <section
        className="workbench-execution"
        data-compact={compact ? "true" : undefined}
        aria-label="Latest execution"
      >
      {!compact && (
        <div className="workbench-subheading workbench-subheading--row">
          <div>
            <h3>Results from {queryLabel}</h3>
            <p>
              {result.rowCount.returned} {result.rowCount.returned === 1 ? "row" : "rows"} returned in {execution.durationMs} ms.
            </p>
          </div>
          <div className="workbench-execution__status">
            {resultIsStale && <Tag type="warm-gray">Stale — editor has changes</Tag>}
            <Tag type="green">Succeeded</Tag>
          </div>
        </div>
      )}
      {result.rowCount.truncated && (
        <p className="workbench-execution__notice">
          Results were truncated
          {result.rowCount.truncationReason
            ? ` (${result.rowCount.truncationReason})`
            : ""}
          .
        </p>
      )}
      {resultWarnings.length > 0 && (
        <div className="workbench-execution__warnings">
          {resultWarnings.map((warning, index) => (
            <InlineNotification
              key={`${execution.executionId}-warning-${index}`}
              lowContrast
              hideCloseButton
              kind="warning"
              title="Returned values need review"
              subtitle={warning}
            />
          ))}
        </div>
      )}
      {result.rows.length === 0 ? (
        <p className="workbench-empty-note">
          The query ran successfully but matched no rows. Review the filter values and
          joins, then run it again.
        </p>
      ) : (
        <div className="workbench-execution__table-wrap workbench-execution__table-wrap--bounded">
          <table>
            <caption>Execution {execution.ordinal} results</caption>
            <thead>
              <tr>
                {columnOrder.map((sourceIndex) => (
                  <th key={`${result.columns[sourceIndex]!.ordinal}-${sourceIndex}`} scope="col">
                    {result.columns[sourceIndex]!.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, rowIndex) => (
                <tr key={`${execution.executionId}-${firstVisibleRow + rowIndex}`}>
                  {columnOrder.map((sourceIndex) => (
                    <td key={`${sourceIndex}-${firstVisibleRow + rowIndex}`}>
                      {renderTaggedCell(row[sourceIndex])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result.rows.length > boundedPageSize && (
        <nav className="workbench-execution__pagination" aria-label="Result pages">
          <p>
            Showing {firstVisibleRow + 1}–{Math.min(firstVisibleRow + boundedPageSize, result.rows.length)} of {result.rows.length} returned rows
          </p>
          <Button
            type="button"
            kind="ghost"
            size="sm"
            disabled={safePage === 0}
            onClick={() =>
              setPageState({
                executionId: execution.executionId,
                page: Math.max(0, safePage - 1),
              })
            }
          >
            Previous result page
          </Button>
          <Button
            type="button"
            kind="ghost"
            size="sm"
            disabled={safePage >= pageCount - 1}
            onClick={() =>
              setPageState({
                executionId: execution.executionId,
                page: Math.min(pageCount - 1, safePage + 1),
              })
            }
          >
            Next result page
          </Button>
        </nav>
      )}
    </section>
  );
};

export const WorkbenchPanel = ({
  session,
  sql,
  parameters,
  editorCatalog,
  catalogLoadingFailed = false,
  wrapLines,
  busy = null,
  error = null,
  announcement = "",
  checkOutcome = null,
  onOpenValidationDetails,
  sqlEditorFocusRequestId = 0,
  showExecutionResult = true,
  onSqlChange,
  onParametersChange,
  onWrapLinesChange,
  onClearDraft,
  onRestoreCurrentVersion,
  onValidate,
  onRun,
}: WorkbenchPanelProps) => {
  const hasSql = sql.trim().length > 0;
  const actionsDisabled = busy !== null || !hasSql;
  const clearDisabled = busy !== null || (!hasSql && parameters.length === 0);
  const relations = workbenchCatalogRelations(editorCatalog);

  return (
    <section className="query-card workbench-panel" aria-labelledby="workbench-title">
      {/*
        The thread above already says which session this is and which query is
        current, so the panel names itself once and quietly.
      */}
      <div className="workbench-panel__heading">
        <h2 id="workbench-title">
          {session.currentVersion
            ? `Editing Query v${session.currentVersion.ordinal}`
            : "Editing draft"}
        </h2>
      </div>

      {error && (
        <InlineNotification
          lowContrast
          hideCloseButton
          kind="error"
          title="Workbench action failed"
          subtitle={error}
        />
      )}

      <p
        className="visually-hidden"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {announcement}
      </p>

      {!session.currentVersion && session.draftSeed && (
        <InlineNotification
          lowContrast
          hideCloseButton
          kind="warning"
          title="Unresolved model draft"
          subtitle="SQL and typed values were recovered from raw model output. Fill any blank parameter names and review the draft before validating or running it."
        />
      )}

      <div className="workbench-editor">
        <SqlEditor
          label="SQL query"
          value={sql}
          onChange={onSqlChange}
          catalog={relations}
          readOnly={busy !== null}
          wrapLines={wrapLines}
          onWrapLinesChange={onWrapLinesChange}
          focusRequestId={sqlEditorFocusRequestId}
        />
        {catalogLoadingFailed && (
          <p className="workbench-editor__catalog-note" role="status">
            Catalog completion is unavailable. SQL editing, validation, and execution
            are still available.
          </p>
        )}
      </div>

      <div className="workbench-actions" aria-label="Workbench actions">
        <Button
          type="button"
          kind="ghost"
          disabled={clearDisabled}
          onClick={onClearDraft}
        >
          Clear draft
        </Button>
        {!hasSql && session.currentVersion && (
          <Button
            type="button"
            kind="ghost"
            disabled={busy !== null}
            onClick={onRestoreCurrentVersion}
          >
            Restore Query v{session.currentVersion.ordinal}
          </Button>
        )}
        {/*
          This saves the editor as an immutable version and checks it, which
          is why it does not say "Validate": the version is the point, and
          the check is what the save reports back.
        */}
        <Button
          type="button"
          kind="secondary"
          disabled={actionsDisabled}
          aria-busy={busy === "validating"}
          onClick={onValidate}
        >
          {busy === "validating" ? "Saving version…" : "Save version & check"}
        </Button>
        <Button
          type="button"
          disabled={actionsDisabled}
          aria-busy={busy === "running"}
          onClick={onRun}
        >
          Run query
        </Button>
        {checkOutcome ? (
          <p className="workbench-actions__outcome" role="status">
            <span data-status={checkOutcome.status}>
              {checkOutcome.status === "valid"
                ? "✓ No findings"
                : `${checkOutcome.findings} ${checkOutcome.findings === 1 ? "finding" : "findings"}`}
            </span>
            {checkOutcome.findings > 0 && onOpenValidationDetails && (
              <button type="button" onClick={onOpenValidationDetails}>
                What was found
              </button>
            )}
            <span className="workbench-actions__note">
              Findings never block a run.
            </span>
          </p>
        ) : (
          <p className="workbench-actions__note">
            Saving keeps every version. Findings never block a run.
          </p>
        )}
      </div>

      <ParameterEditor
        parameters={parameters}
        disabled={busy !== null}
        onChange={onParametersChange}
      />
      {showExecutionResult && (
        <ExecutionResult session={session} sql={sql} parameters={parameters} />
      )}
    </section>
  );
};
