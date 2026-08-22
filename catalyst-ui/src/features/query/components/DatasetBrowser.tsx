import { Button, InlineLoading } from "@carbon/react";
import { useEffect, useMemo, useState } from "react";
import type { CatalystApi } from "../api";
import type {
  DatasetOverview,
  DatasetRows,
  WorkbenchEditorCatalog,
} from "../types";
import "./DatasetBrowser.css";

interface DatasetBrowserProps {
  api: CatalystApi;
  catalog?: WorkbenchEditorCatalog | null;
  catalogLoadingFailed?: boolean;
  dataSourceId?: string;
  /** Insert a column name at the cursor in the SQL editor. */
  onInsertColumn?: (columnName: string) => void;
}

const displayDate = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
};

const displayDateRange = (first: string | null, last: string | null) => {
  if (!first && !last) return "No observations";
  if (!first || first === last) return displayDate(last ?? first);
  if (!last) return displayDate(first);
  return `${displayDate(first)} – ${displayDate(last)}`;
};

const shortPatient = (value: string) => value.length > 12 ? value.slice(0, 12) : value;

const stableCompare = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

/**
 * A three-character glyph standing in for the column's type, so the shape of a
 * relation is readable at rail width where the type name would not fit.
 */
const typeGlyph = (logicalType: string) => {
  if (/^(date|time)/.test(logicalType)) return "cal";
  if (/(int|decimal|number|numeric|float|double)/.test(logicalType)) return "123";
  if (/bool/.test(logicalType)) return "t/f";
  return "abc";
};

export const DatasetBrowser = ({
  api,
  catalog = null,
  catalogLoadingFailed = false,
  dataSourceId,
  onInsertColumn,
}: DatasetBrowserProps) => {
  const [overview, setOverview] = useState<DatasetOverview | null>(null);
  const [rows, setRows] = useState<DatasetRows | null>(null);
  const [testName, setTestName] = useState("");
  const [patientId, setPatientId] = useState("");
  const [loading, setLoading] = useState(Boolean(api.getDatasetOverview));
  const [message, setMessage] = useState<string | null>(null);
  const [columnFilter, setColumnFilter] = useState("");
  const [relationName, setRelationName] = useState("");

  const catalogRelations = useMemo(
    () => (catalog?.schemas ?? [])
      .flatMap((schema) => schema.views.map((view) => ({ schema, view })))
      .sort((left, right) =>
        stableCompare(left.view.qualifiedName, right.view.qualifiedName),
      ),
    [catalog],
  );

  const selectedRelation =
    catalogRelations.find(
      ({ view }) => view.qualifiedName === relationName,
    ) ?? catalogRelations[0] ?? null;

  const filter = columnFilter.trim().toLowerCase();
  const visibleColumns = useMemo(() => {
    const columns = selectedRelation?.view.columns ?? [];
    return filter
      ? columns.filter((column) => column.name.toLowerCase().includes(filter))
      : columns;
  }, [filter, selectedRelation]);

  const loadRows = async (offset = 0) => {
    if (!api.getDatasetRows) return;
    setLoading(true);
    setRows(null);
    try {
      setRows(
        await api.getDatasetRows({
          testName: testName || undefined,
          patientId: patientId.trim() || undefined,
          limit: 25,
          offset,
          dataSourceId,
        }),
      );
      setMessage(null);
    } catch (error) {
      setRows(null);
      setMessage(error instanceof Error ? error.message : "Dataset rows are unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!api.getDatasetOverview || !api.getDatasetRows) return;
    const controller = new AbortController();
    Promise.all([
      api.getDatasetOverview(dataSourceId, controller.signal),
      api.getDatasetRows({ limit: 25, offset: 0, dataSourceId }, controller.signal),
    ])
      .then(([nextOverview, nextRows]) => {
        setOverview(nextOverview);
        setRows(nextRows);
        setMessage(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setMessage(error instanceof Error ? error.message : "Dataset is unavailable.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, dataSourceId]);

  if (!api.getDatasetOverview && !catalog) return null;

  const totalColumns = selectedRelation?.view.columns.length ?? 0;

  return (
    <div className="dataset-browser" aria-label="Available data">
      <div className="dataset-browser__controls">
        <label className="visually-hidden" htmlFor="catalyst-column-filter">
          Filter columns
        </label>
        <input
          id="catalyst-column-filter"
          value={columnFilter}
          placeholder="Filter columns"
          onChange={(event) => setColumnFilter(event.currentTarget.value)}
        />
        {catalogRelations.length > 1 && (
          <>
            <label className="visually-hidden" htmlFor="catalyst-relation">
              Relation
            </label>
            <select
              id="catalyst-relation"
              value={selectedRelation?.view.qualifiedName ?? ""}
              onChange={(event) => setRelationName(event.currentTarget.value)}
            >
              {catalogRelations.map(({ view }) => (
                <option key={view.qualifiedName} value={view.qualifiedName}>
                  {view.qualifiedName}
                </option>
              ))}
            </select>
          </>
        )}
        {selectedRelation && (
          <p className="dataset-browser__count">
            {filter
              ? `${visibleColumns.length} of ${totalColumns} columns`
              : `${totalColumns} ${totalColumns === 1 ? "column" : "columns"}`}
            {catalog?.dialect ? ` · ${catalog.dialect}` : ""}
          </p>
        )}
      </div>

      {catalogLoadingFailed && (
        <p className="dataset-browser__message" role="status">
          The queryable database schema is unavailable. Record preview may still
          work.
        </p>
      )}
      {!catalog && !catalogLoadingFailed && (
        <InlineLoading description="Loading queryable database schema…" />
      )}
      {catalog && catalogRelations.length === 0 && (
        <p className="dataset-browser__message" role="status">
          No queryable relations were returned by the catalog.
        </p>
      )}

      {selectedRelation && (
        <div className="dataset-browser__columns">
          <table>
            <caption className="visually-hidden">
              Columns in {selectedRelation.view.qualifiedName}
            </caption>
            <thead>
              <tr>
                <th scope="col" className="dataset-browser__col-type">Type</th>
                <th scope="col">Column</th>
                <th scope="col" className="dataset-browser__col-wide">Nullable</th>
                <th scope="col" className="dataset-browser__col-wide">Unit relationship</th>
                <th scope="col" className="dataset-browser__col-wide">Description</th>
              </tr>
            </thead>
            <tbody>
              {visibleColumns.map((column) => (
                <tr key={column.name}>
                  <td className="dataset-browser__col-type">
                    <span
                      className="dataset-browser__glyph"
                      title={column.logicalType}
                    >
                      {typeGlyph(column.logicalType)}
                    </span>
                  </td>
                  <td>
                    {onInsertColumn ? (
                      <button
                        type="button"
                        className="dataset-browser__insert"
                        aria-label={`Insert ${column.name} into the SQL editor`}
                        title={`Insert ${column.name} into the SQL editor`}
                        onClick={() => onInsertColumn(column.name)}
                      >
                        <code>{column.name}</code>
                      </button>
                    ) : (
                      <code>{column.name}</code>
                    )}
                  </td>
                  <td className="dataset-browser__col-wide">
                    {column.nullable ? "Yes" : "No"}
                  </td>
                  <td className="dataset-browser__col-wide">
                    {column.unitColumn ? (
                      <>Unit from <code>{column.unitColumn}</code></>
                    ) : "—"}
                  </td>
                  <td className="dataset-browser__col-wide">{column.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {overview && (
        <details className="dataset-browser__records">
          <summary>Preview available laboratory records</summary>
          <dl className="dataset-browser__metrics">
            <div><dt>Patients</dt><dd>{overview.patients.toLocaleString()}</dd></div>
            <div><dt>Results</dt><dd>{overview.results.toLocaleString()}</dd></div>
            <div><dt>Test types</dt><dd>{overview.testTypes}</dd></div>
            <div>
              <dt>Date range</dt>
              <dd>{displayDateRange(overview.firstObservedAt, overview.lastObservedAt)}</dd>
            </div>
          </dl>
          <div className="dataset-filters">
            <label>
              Test type
              <select value={testName} onChange={(event) => setTestName(event.currentTarget.value)}>
                <option value="">All tests</option>
                {overview.tests.map((test) => <option key={test.testName}>{test.testName}</option>)}
              </select>
            </label>
            <label>
              Patient FHIR ID
              <input value={patientId} onChange={(event) => setPatientId(event.currentTarget.value)} placeholder="Optional exact ID" />
            </label>
            <Button kind="secondary" size="sm" disabled={loading} onClick={() => void loadRows(0)}>
              Apply filters
            </Button>
          </div>
          {message && <p className="dataset-browser__message">{message}</p>}
          {rows && (
            rows.total === 0 ? (
              <p className="dataset-browser__empty">
                No laboratory records match these filters.
              </p>
            ) : <>
              <div className="dataset-browser__rows">
                <table>
                  <caption>{rows.total.toLocaleString()} matching laboratory results; showing {rows.offset + 1}–{Math.min(rows.offset + rows.rows.length, rows.total)}</caption>
                  <thead><tr>
                    <th scope="col">Patient</th><th scope="col">Test</th><th scope="col">Value</th>
                    <th scope="col">Observed</th><th scope="col">Turnaround</th>
                  </tr></thead>
                  <tbody>
                    {rows.rows.map((row) => (
                      <tr key={row.observationId}>
                        <td title={row.patientId}>{shortPatient(row.patientId)}</td>
                        <td>{row.testName}</td><td>{row.value ?? "—"} {row.unit ?? ""}</td>
                        <td>{displayDate(row.observedAt)}</td><td>{row.turnaroundMinutes !== null ? `${Math.round(Number(row.turnaroundMinutes))} min` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="dataset-pagination">
                <Button kind="ghost" size="sm" disabled={loading || rows.offset === 0} onClick={() => void loadRows(Math.max(0, rows.offset - rows.limit))}>Previous</Button>
                <Button kind="ghost" size="sm" disabled={loading || rows.offset + rows.limit >= rows.total} onClick={() => void loadRows(rows.offset + rows.limit)}>Next</Button>
              </div>
            </>
          )}
        </details>
      )}

      {selectedRelation && (
        <p className="dataset-browser__grain">
          <strong>Grain</strong> — {selectedRelation.view.grain}
        </p>
      )}
    </div>
  );
};
