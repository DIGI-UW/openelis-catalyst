import {
  InlineNotification,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
} from "@carbon/react";
import type { CatalystTable, TaggedCell } from "../types";

interface ResultsTableProps {
  result: CatalystTable;
}

const renderCell = (cell: TaggedCell) => {
  switch (cell.type) {
    case "null":
      return <span aria-label="No value">—</span>;
    case "boolean":
      return cell.value ? "Yes" : "No";
    default:
      return String(cell.value);
  }
};

const rowSummary = (result: CatalystTable) => {
  const { returned, total, totalIsExact } = result.table.rowCount;
  if (returned === 0) return "0 rows returned";
  if (total === null) return `${returned} rows returned; total unknown`;
  if (totalIsExact && total !== returned) {
    return `${returned} of ${total} rows returned`;
  }
  return `${returned} ${returned === 1 ? "row" : "rows"} returned`;
};

export const ResultsTable = ({ result }: ResultsTableProps) => {
  const { columns, rows, rowCount } = result.table;

  return (
    <section
      className="query-card results"
      aria-label="Query results"
    >
      <div className="section-heading section-heading--row">
        <div>
          <p className="eyebrow">Governed query output</p>
          <h2 id="results-title">Results</h2>
        </div>
        <Tag type="green">Succeeded</Tag>
      </div>

      {rowCount.truncated && (
        <InlineNotification
          lowContrast
          hideCloseButton
          kind="warning"
          title={`Results truncated at ${rowCount.limit} rows.`}
          subtitle="Refine the question to retrieve a smaller result set."
        />
      )}

      {result.warnings.map((warning) => (
        <InlineNotification
          key={warning}
          lowContrast
          hideCloseButton
          kind="warning"
          title={warning}
        />
      ))}

      {rows.length === 0 ? (
        <div className="empty-state">
          <h3>No rows matched this query.</h3>
          <p>The query completed successfully and returned an empty table.</p>
        </div>
      ) : (
        <TableContainer>
          <Table size="lg" useZebraStyles>
            <TableHead>
              <TableRow>
                {columns.map((column) => (
                  <TableHeader key={column.name}>
                    {column.unit
                      ? `${column.name} (${column.unit})`
                      : column.name}
                  </TableHeader>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row, rowIndex) => (
                <TableRow key={`${result.preview.previewId}-${rowIndex}`}>
                  {row.map((cell, columnIndex) => (
                    <TableCell key={`${columnIndex}-${cell.type}`}>
                      {renderCell(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <p className="results__summary">{rowSummary(result)}</p>
    </section>
  );
};
