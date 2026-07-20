import { Accordion, AccordionItem, Button, InlineLoading, Tag } from "@carbon/react";
import { useEffect, useMemo, useState } from "react";
import type { CatalystApi } from "../api";
import type {
  DatasetOverview,
  DatasetRows,
  WorkbenchEditorCatalog,
} from "../types";

interface DatasetBrowserProps {
  api: CatalystApi;
  catalog?: WorkbenchEditorCatalog | null;
  catalogLoadingFailed?: boolean;
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

export const DatasetBrowser = ({
  api,
  catalog = null,
  catalogLoadingFailed = false,
}: DatasetBrowserProps) => {
  const [overview, setOverview] = useState<DatasetOverview | null>(null);
  const [rows, setRows] = useState<DatasetRows | null>(null);
  const [testName, setTestName] = useState("");
  const [patientId, setPatientId] = useState("");
  const [loading, setLoading] = useState(Boolean(api.getDatasetOverview));
  const [message, setMessage] = useState<string | null>(null);
  const catalogRelations = useMemo(
    () => (catalog?.schemas ?? [])
      .flatMap((schema) => schema.views.map((view) => ({ schema, view })))
      .sort((left, right) =>
        stableCompare(left.view.qualifiedName, right.view.qualifiedName),
      ),
    [catalog],
  );

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
      api.getDatasetOverview(controller.signal),
      api.getDatasetRows({ limit: 25, offset: 0 }, controller.signal),
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
  }, [api]);

  if (!api.getDatasetOverview) return null;

  return (
    <section className="query-card dataset-browser" aria-labelledby="dataset-title">
      <div className="section-heading section-heading--row">
        <div>
          <p className="eyebrow">Know what to ask</p>
          <h1 id="dataset-title">Available OpenELIS laboratory data</h1>
          <p>
            Explore the laboratory records currently available to Catalyst from
            OpenELIS through FHIR before forming a question.
          </p>
        </div>
        <Tag type="purple">OpenELIS → FHIR</Tag>
      </div>

      {loading && !overview && <InlineLoading description="Loading dataset…" />}
      {message && <p className="dataset-browser__message">{message}</p>}

      {overview && (
        <>
          <dl className="dataset-metrics">
            <div><dt>Patients</dt><dd>{overview.patients.toLocaleString()}</dd></div>
            <div><dt>Results</dt><dd>{overview.results.toLocaleString()}</dd></div>
            <div><dt>Test types</dt><dd>{overview.testTypes}</dd></div>
            <div>
              <dt>Date range</dt>
              <dd>{displayDateRange(overview.firstObservedAt, overview.lastObservedAt)}</dd>
            </div>
          </dl>

        </>
      )}

      <section
        className="dataset-schema"
        aria-labelledby="dataset-schema-title"
      >
        <div className="dataset-schema__heading">
          <h2 id="dataset-schema-title">Supported query schema</h2>
          <p>
            {catalogRelations.length > 0
              ? `${catalogRelations.length.toLocaleString()} ${catalogRelations.length === 1 ? "relation" : "relations"} available. Expand a relation to see its columns.`
              : "Tables and columns available to generated and manually edited SQL."}
          </p>
        </div>
        {catalogLoadingFailed && (
          <p className="dataset-browser__message" role="status">
            The queryable database schema is unavailable. Record preview may still work.
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
        {catalogRelations.length > 0 && (
          <Accordion className="dataset-schema__relations">
            {catalogRelations.map(({ schema, view }) => (
              <AccordionItem
                key={`${schema.name}.${view.name}`}
                title={(
                  <span className="dataset-schema__relation-title">
                    <code>{view.qualifiedName}</code>
                    {" "}
                    <span>{view.columns.length.toLocaleString()} {view.columns.length === 1 ? "column" : "columns"}</span>
                  </span>
                )}
              >
                <div className="dataset-schema__relation-details">
                  <div className="dataset-schema__relation-meta">
                    <p className="dataset-schema__grain">
                      <strong>Grain:</strong> {view.grain}
                    </p>
                    <Tag type="cool-gray">{catalog?.dialect}</Tag>
                  </div>
                  <div className="dataset-schema__table-wrap">
                    <table>
                      <caption>Columns in {view.qualifiedName}</caption>
                      <thead>
                        <tr>
                          <th scope="col">Column</th>
                          <th scope="col">Type</th>
                          <th scope="col">Nullable</th>
                          <th scope="col">Unit relationship</th>
                          <th scope="col">Description</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.columns.map((column) => (
                          <tr key={column.name}>
                            <td><code>{column.name}</code></td>
                            <td>{column.logicalType}</td>
                            <td>{column.nullable ? "Yes" : "No"}</td>
                            <td>
                              {column.unitColumn ? (
                                <>Unit from <code>{column.unitColumn}</code></>
                              ) : "—"}
                            </td>
                            <td>{column.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </section>

      {overview && (
        <Accordion className="dataset-browser__details">
          <AccordionItem title="Preview available laboratory records">
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
          </AccordionItem>
        </Accordion>
      )}
    </section>
  );
};
