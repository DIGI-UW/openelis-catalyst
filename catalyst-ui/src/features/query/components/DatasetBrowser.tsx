import { Button, InlineLoading, Tag } from "@carbon/react";
import { useEffect, useState } from "react";
import type { CatalystApi } from "../api";
import type { DatasetOverview, DatasetRows } from "../types";

interface DatasetBrowserProps {
  api: CatalystApi;
  onQuestionSelect: (question: string) => void;
  disabled?: boolean;
}

const displayDate = (value: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(value));

const shortPatient = (value: string) => value.length > 12 ? value.slice(0, 12) : value;

export const DatasetBrowser = ({
  api,
  onQuestionSelect,
  disabled = false,
}: DatasetBrowserProps) => {
  const [overview, setOverview] = useState<DatasetOverview | null>(null);
  const [rows, setRows] = useState<DatasetRows | null>(null);
  const [testName, setTestName] = useState("");
  const [patientId, setPatientId] = useState("");
  const [loading, setLoading] = useState(Boolean(api.getDatasetOverview));
  const [message, setMessage] = useState<string | null>(null);

  const loadRows = async (offset = 0) => {
    if (!api.getDatasetRows) return;
    setLoading(true);
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
          <p className="eyebrow">Know what you can ask</p>
          <h1 id="dataset-title">Synthetic laboratory dataset</h1>
          <p>
            Browse the exact OpenELIS records available to Catalyst before forming a
            question. Values are synthetic and are not for clinical decisions.
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
              <dd>{displayDate(overview.firstObservedAt)} – {displayDate(overview.lastObservedAt)}</dd>
            </div>
          </dl>

          <div className="dataset-browser__summary-table">
            <table>
              <caption>Test types and numeric distributions in the synthetic cohort</caption>
              <thead>
                <tr>
                  <th scope="col">Test</th><th scope="col">Unit</th>
                  <th scope="col">Results</th><th scope="col">Patients</th>
                  <th scope="col">Min</th><th scope="col">Median</th><th scope="col">Max</th>
                </tr>
              </thead>
              <tbody>
                {overview.tests.map((test) => (
                  <tr key={test.testName}>
                    <th scope="row">{test.testName}</th><td>{test.unit ?? "—"}</td>
                    <td>{test.results}</td><td>{test.patients}</td>
                    <td>{test.minimum ?? "—"}</td><td>{test.median ?? "—"}</td>
                    <td>{test.maximum ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="example-questions" aria-labelledby="examples-title">
            <h2 id="examples-title">Example questions</h2>
            <div>
              {overview.exampleQuestions.map((question) => (
                <Button
                  key={question}
                  kind="ghost"
                  size="sm"
                  disabled={disabled}
                  onClick={() => onQuestionSelect(question)}
                >
                  {question}
                </Button>
              ))}
            </div>
          </div>
        </>
      )}

      {overview && (
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
      )}

      {rows && (
        <>
          <div className="dataset-browser__rows">
            <table>
              <caption>{rows.total.toLocaleString()} matching laboratory results; showing {rows.offset + 1}–{Math.min(rows.offset + rows.rows.length, rows.total)}</caption>
              <thead><tr>
                <th scope="col">Patient</th><th scope="col">Test</th><th scope="col">Value</th>
                <th scope="col">Observed</th><th scope="col">Turnaround</th>
              </tr></thead>
              <tbody>
                {rows.rows.map((row) => (
                  <tr key={`${row.patientId}-${row.testName}-${row.observedAt}`}>
                    <td title={row.patientId}>{shortPatient(row.patientId)}</td>
                    <td>{row.testName}</td><td>{row.value ?? "—"} {row.unit ?? ""}</td>
                    <td>{displayDate(row.observedAt)}</td><td>{row.turnaroundMinutes ? `${Math.round(Number(row.turnaroundMinutes))} min` : "—"}</td>
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
    </section>
  );
};
