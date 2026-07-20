import { Button, InlineNotification, Tag } from "@carbon/react";
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

const stableCompare = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

interface WorkbenchPanelProps {
  session: WorkbenchSession;
  sql: string;
  parameters: BoundParameter[];
  editorCatalog?: WorkbenchEditorCatalog | null;
  catalogLoadingFailed?: boolean;
  wrapLines: boolean;
  busy?: "generating" | "validating" | "running" | null;
  error?: string | null;
  onSqlChange: (sql: string) => void;
  onParametersChange: (parameters: BoundParameter[]) => void;
  onWrapLinesChange: (wrapLines: boolean) => void;
  onClearDraft: () => void;
  onRestoreCurrentVersion: () => void;
  onNewSession: () => void;
  onValidate: () => void;
  onRun: () => void;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const recordAt = (
  source: Record<string, unknown> | undefined,
  key: string,
) => {
  const value = source?.[key];
  return isRecord(value) ? value : undefined;
};

const textAt = (
  source: Record<string, unknown> | undefined,
  key: string,
) => {
  const value = source?.[key];
  return typeof value === "string" && value ? value : undefined;
};

const arrayAt = (
  source: Record<string, unknown> | undefined,
  key: string,
) => {
  const value = source?.[key];
  return Array.isArray(value) ? value : [];
};

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

const validationLabel = (status: string) =>
  `${status.charAt(0).toUpperCase()}${status.slice(1)}`;

const GenerationEvidence = ({ session }: { session: WorkbenchSession }) => {
  const provenance = session.provenance;
  const outcome = recordAt(provenance, "generationOutcome");
  const diagnostic = recordAt(outcome, "diagnosticCandidate");
  const candidate = recordAt(diagnostic, "candidate");
  const candidateSql = textAt(candidate, "sql");
  const rawOutput =
    textAt(provenance, "generationRawOutput") ??
    textAt(diagnostic, "rawOutput") ??
    textAt(outcome, "rawOutput");
  const attempts = arrayAt(diagnostic, "attempts").filter(isRecord);
  const collaboration = recordAt(outcome, "modelCollaboration");
  const writer = recordAt(collaboration, "writer");
  const reviewer = recordAt(collaboration, "reviewer");

  if (!candidate && !rawOutput && attempts.length === 0 && !collaboration) return null;

  return (
    <section className="workbench-evidence" aria-labelledby="model-evidence-title">
      <div className="workbench-subheading">
        <h3 id="model-evidence-title">Generation evidence</h3>
        <p>Retained model artifacts and structured diagnostics; no hidden reasoning trace.</p>
      </div>
      <div className="workbench-evidence__grid">
        {writer && (
          <section
            className="workbench-evidence__artifact"
            aria-label="Writer candidate"
          >
            <h4>Writer candidate</h4>
            <p>Model: <strong>{textAt(writer, "model") ?? "Not recorded"}</strong></p>
            <pre>{textAt(recordAt(writer, "candidate"), "sql") ?? "No SQL returned"}</pre>
            {arrayAt(recordAt(writer, "candidate"), "parameters").length > 0 && (
              <details>
                <summary>Writer parameters</summary>
                <pre>{JSON.stringify(arrayAt(recordAt(writer, "candidate"), "parameters"), null, 2)}</pre>
              </details>
            )}
            {arrayAt(writer, "lintFindings").length > 0 && (
              <ul>
                {arrayAt(writer, "lintFindings").filter(isRecord).map((finding, index) => (
                  <li key={`${textAt(finding, "code") ?? "writer-finding"}-${index}`}>
                    {textAt(finding, "message") ?? "Writer lint finding"}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {reviewer && (
          <section
            className="workbench-evidence__artifact"
            aria-label="Reviewer correction"
          >
            <h4>Reviewer correction</h4>
            <p>Model: <strong>{textAt(reviewer, "model") ?? "Not recorded"}</strong></p>
            <p>Decision: {textAt(reviewer, "decision") ?? "Not recorded"}</p>
            {recordAt(reviewer, "candidate") && (
              <>
                <pre>{textAt(recordAt(reviewer, "candidate"), "sql") ?? "No SQL returned"}</pre>
                {arrayAt(recordAt(reviewer, "candidate"), "parameters").length > 0 && (
                  <details>
                    <summary>Reviewer parameters</summary>
                    <pre>{JSON.stringify(arrayAt(recordAt(reviewer, "candidate"), "parameters"), null, 2)}</pre>
                  </details>
                )}
              </>
            )}
            {arrayAt(reviewer, "checks").length > 0 && (
              <ul>
                {arrayAt(reviewer, "checks").filter(isRecord).map((check, index) => (
                  <li key={`${textAt(check, "name") ?? "review-check"}-${index}`}>
                    {textAt(check, "message") ?? textAt(check, "name") ?? "Reviewer check"}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {candidate && (
          <section
            className="workbench-evidence__artifact"
            aria-label="Model candidate"
          >
            <h4>Model candidate</h4>
            {candidateSql ? (
              <pre>{candidateSql}</pre>
            ) : (
              <p>The candidate did not contain SQL.</p>
            )}
            {arrayAt(candidate, "parameters").length > 0 && (
              <details>
                <summary>Candidate parameters</summary>
                <pre>{JSON.stringify(arrayAt(candidate, "parameters"), null, 2)}</pre>
              </details>
            )}
          </section>
        )}
        {rawOutput && (
          <section
            className="workbench-evidence__artifact"
            aria-label="Raw model output"
          >
            <h4>Raw model output</h4>
            <pre>{rawOutput}</pre>
          </section>
        )}
      </div>
      {attempts.length > 0 && (
        <div className="workbench-attempts">
          <h4>Generation attempts</h4>
          <ol>
            {attempts.map((attempt, attemptIndex) => {
              const attemptNumber = attempt.attempt ?? attemptIndex + 1;
              const attemptStatus = textAt(attempt, "status") ?? "unknown";
              const findings = arrayAt(attempt, "findings").filter(isRecord);
              return (
                <li key={`${String(attemptNumber)}-${attemptIndex}`}>
                  <strong>{`Attempt ${String(attemptNumber)} — ${attemptStatus}`}</strong>
                  {findings.length > 0 && (
                    <ul>
                      {findings.map((finding, findingIndex) => (
                        <li key={`${textAt(finding, "path") ?? "finding"}-${findingIndex}`}>
                          {textAt(finding, "path") && (
                            <code>{textAt(finding, "path")}</code>
                          )}
                          <span>
                            {textAt(finding, "message") ?? "Generation finding"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
};

const ValidationSummary = ({ session }: { session: WorkbenchSession }) => {
  const validation = session.latestValidation;
  return (
    <section className="workbench-validation" aria-labelledby="validation-title">
      <div className="workbench-subheading workbench-subheading--row">
        <div>
          <h3 id="validation-title">Validation</h3>
          <p>
            Validation is advisory. Findings guide manual edits and never prevent a run.
          </p>
        </div>
        <span
          className={`workbench-validation__status workbench-validation__status--${validation?.status ?? "not-run"}`}
        >
          {validation ? validationLabel(validation.status) : "Not validated"}
        </span>
      </div>
      {validation && (
        <>
          <dl className="workbench-inline-metadata">
            <div>
              <dt>Validator</dt>
              <dd>{validation.validatorRevision}</dd>
            </div>
            <div>
              <dt>Validated digest</dt>
              <dd>{validation.queryDigest}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{validation.durationMs} ms</dd>
            </div>
          </dl>
          {validation.findings.length > 0 ? (
            <ul className="workbench-findings">
              {validation.findings.map((finding) => (
                <li key={finding.findingId} data-severity={finding.severity}>
                  <div>
                    <strong>{finding.ruleCode}</strong>
                    <Tag size="sm" type={finding.severity === "error" ? "red" : "purple"}>
                      {finding.severity}
                    </Tag>
                  </div>
                  <p>{finding.message}</p>
                  <code>{finding.path}</code>
                  {finding.suggestedAction && (
                    <p className="workbench-findings__suggestion">
                      Suggested action: {finding.suggestedAction}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="workbench-empty-note">No validation findings.</p>
          )}
        </>
      )}
    </section>
  );
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
          <p>Names must match the SQL placeholders exactly.</p>
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
        <p className="workbench-empty-note">This query has no bound parameters.</p>
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

const ProvenanceSummary = ({ session }: { session: WorkbenchSession }) => {
  const profileSnapshot = recordAt(session.provenance, "profileSnapshot");
  const profileLabel = textAt(profileSnapshot, "profileLabel") ?? session.profileId;
  const roleModels = recordAt(profileSnapshot, "roleModels");
  const currentVersion = session.currentVersion;

  return (
    <section className="workbench-provenance" aria-labelledby="workbench-provenance-title">
      <div className="workbench-subheading">
        <h3 id="workbench-provenance-title">Run provenance</h3>
        <p>Profile, model, dataset, and immutable query identifiers.</p>
      </div>
      <dl className="workbench-provenance__grid">
        <div>
          <dt>Profile</dt>
          <dd>{profileLabel}</dd>
        </div>
        <div>
          <dt>Profile ID</dt>
          <dd>{session.profileId}</dd>
        </div>
        <div>
          <dt>Session</dt>
          <dd>{session.sessionId}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>{currentVersion?.versionId ?? "No version"}</dd>
        </div>
        <div>
          <dt>Query digest</dt>
          <dd>{currentVersion?.queryDigest ?? "Not available"}</dd>
        </div>
        <div>
          <dt>Catalog</dt>
          <dd>{session.catalogVersion}</dd>
        </div>
        <div>
          <dt>Dataset</dt>
          <dd>{session.datasetId}</dd>
        </div>
        <div>
          <dt>Dataset version</dt>
          <dd>{session.datasetVersion}</dd>
        </div>
        {roleModels &&
          Object.entries(roleModels)
            .filter((entry): entry is [string, string] => typeof entry[1] === "string")
            .sort(([left], [right]) => stableCompare(left, right))
            .map(([role, model]) => (
              <div key={role}>
                <dt>{role.replaceAll("_", " ")}</dt>
                <dd>{model}</dd>
              </div>
            ))}
      </dl>
    </section>
  );
};

const VersionHistory = ({ session }: { session: WorkbenchSession }) => (
  <section className="workbench-history" aria-labelledby="version-history-title">
    <div className="workbench-subheading">
      <h3 id="version-history-title">Version history</h3>
      <p>Every validation and run references an immutable query version.</p>
    </div>
    {session.versions.length === 0 ? (
      <p className="workbench-empty-note">No query version has been recovered yet.</p>
    ) : (
      <ol reversed>
        {[...session.versions]
          .sort((left, right) => right.ordinal - left.ordinal)
          .map((version) => {
            const model = textAt(version.provenance, "model");
            const collaborationRole = textAt(version.provenance, "collaborationRole");
            return (
            <li
              key={version.versionId}
              aria-current={version.versionId === session.currentVersionId ? "true" : undefined}
            >
              <div>
                <strong>Version {version.ordinal}</strong>
                {version.versionId === session.currentVersionId && (
                  <Tag size="sm" type="blue">Current</Tag>
                )}
              </div>
              <dl>
                <div>
                  <dt>Author</dt>
                  <dd>{version.authorType.replaceAll("_", " ")}</dd>
                </div>
                <div>
                  <dt>Version ID</dt>
                  <dd>{version.versionId}</dd>
                </div>
                <div>
                  <dt>Digest</dt>
                  <dd>{version.queryDigest}</dd>
                </div>
                {collaborationRole && (
                  <div>
                    <dt>Model role</dt>
                    <dd>{collaborationRole}</dd>
                  </div>
                )}
                {model && (
                  <div>
                    <dt>Model</dt>
                    <dd>{model}</dd>
                  </div>
                )}
              </dl>
              <details>
                <summary>View query version</summary>
                <pre>{version.sql}</pre>
                {version.parameters.length > 0 && (
                  <pre>{JSON.stringify(version.parameters, null, 2)}</pre>
                )}
              </details>
            </li>
            );
          })}
      </ol>
    )}
  </section>
);

const latestExecution = (executions: WorkbenchExecution[]) =>
  executions.reduce<WorkbenchExecution | null>(
    (latest, execution) =>
      latest === null || execution.ordinal > latest.ordinal ? execution : latest,
    null,
  );

const ExecutionResult = ({
  session,
  sql,
  parameters,
}: {
  session: WorkbenchSession;
  sql: string;
  parameters: BoundParameter[];
}) => {
  const execution = latestExecution(session.executions);
  if (!execution) return null;
  const executionVersion = session.versions.find(
    (version) => version.versionId === execution.versionId,
  ) ?? null;
  const queryLabel = executionVersion
    ? `Query v${executionVersion.ordinal}`
    : "query version unavailable";
  const editorMatchesExecution = executionVersion
    ? editorContentMatchesVersion(
        {
          sql,
          parameters,
          expectedColumns: executionVersion.expectedColumns,
        },
        executionVersion,
      )
    : sql === execution.query.sql &&
      JSON.stringify(parameters) === JSON.stringify(execution.query.parameters);
  const resultIsStale =
    session.currentVersionId !== execution.versionId || !editorMatchesExecution;

  if (execution.status === "failed") {
    const diagnostic = execution.databaseDiagnostic;
    return (
      <section className="workbench-execution" aria-label="Latest execution">
        <div className="workbench-subheading workbench-subheading--row">
          <div>
            <h3>Execution failed for {queryLabel}</h3>
            <p>Execution {execution.ordinal}</p>
          </div>
          <Tag type="red">Failed</Tag>
        </div>
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
      <section className="workbench-execution" aria-label="Latest execution">
        <h3>Results from {queryLabel}</h3>
        <p>The database reported success without a tabular result.</p>
      </section>
    );
  }
  const columnOrder = result.columns
    .map((_, index) => index)
    .sort((left, right) => result.columns[left]!.ordinal - result.columns[right]!.ordinal);
  const resultWarnings = executionResultWarnings(result);

  return (
    <section className="workbench-execution" aria-label="Latest execution">
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
              {result.rows.map((row, rowIndex) => (
                <tr key={`${execution.executionId}-${rowIndex}`}>
                  {columnOrder.map((sourceIndex) => (
                    <td key={`${sourceIndex}-${rowIndex}`}>
                      {renderTaggedCell(row[sourceIndex])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
  onSqlChange,
  onParametersChange,
  onWrapLinesChange,
  onClearDraft,
  onRestoreCurrentVersion,
  onNewSession,
  onValidate,
  onRun,
}: WorkbenchPanelProps) => {
  const hasSql = sql.trim().length > 0;
  const actionsDisabled = busy !== null || !hasSql;
  const clearDisabled = busy !== null || (!hasSql && parameters.length === 0);
  const relations = workbenchCatalogRelations(editorCatalog);

  return (
    <section className="query-card workbench-panel" aria-labelledby="workbench-title">
      <div className="section-heading section-heading--row workbench-panel__heading">
        <div>
          <p className="eyebrow">Editable SQL research loop</p>
          <h2 id="workbench-title">Query workbench</h2>
          <p>
            Review, edit, validate, and run the exact SQL against the connected
            OpenELIS data projection.
          </p>
        </div>
        <div className="workbench-panel__session-actions">
          <Tag type="blue">Session active</Tag>
          <Button
            type="button"
            kind="ghost"
            size="sm"
            disabled={busy !== null}
            onClick={onNewSession}
          >
            New session
          </Button>
        </div>
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
        <Button
          type="button"
          kind="secondary"
          disabled={actionsDisabled}
          aria-busy={busy === "validating"}
          onClick={onValidate}
        >
          Validate query
        </Button>
        <Button
          type="button"
          disabled={actionsDisabled}
          aria-busy={busy === "running"}
          onClick={onRun}
        >
          Run query
        </Button>
        <p>Run remains available when validation reports errors.</p>
      </div>

      <ParameterEditor
        parameters={parameters}
        disabled={busy !== null}
        onChange={onParametersChange}
      />
      <ValidationSummary session={session} />

      <ExecutionResult session={session} sql={sql} parameters={parameters} />
      <GenerationEvidence session={session} />
      <div className="workbench-records">
        <ProvenanceSummary session={session} />
        <VersionHistory session={session} />
      </div>
    </section>
  );
};
