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

const preferredColumns = [
  "result_value",
  "result_unit",
  "issued_at",
  "receipt_to_release_minutes",
  "observed_at",
  "test_name",
];

export const ResultsTable = ({ result }: ResultsTableProps) => {
  const { columns, rows, rowCount } = result.table;
  const columnOrder = columns
    .map((_, index) => index)
    .sort((left, right) => {
      const leftPriority = preferredColumns.indexOf(columns[left]!.name);
      const rightPriority = preferredColumns.indexOf(columns[right]!.name);
      const normalizedLeft =
        leftPriority === -1 ? preferredColumns.length + left : leftPriority;
      const normalizedRight =
        rightPriority === -1 ? preferredColumns.length + right : rightPriority;
      return normalizedLeft - normalizedRight;
    });
  const displayColumns = columnOrder.map((index) => columns[index]!);

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
                {displayColumns.map((column) => (
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
                  {columnOrder.map((sourceIndex, columnIndex) => {
                    const cell = row[sourceIndex]!;
                    return (
                      <TableCell
                        key={`${displayColumns[columnIndex]!.name}-${sourceIndex}`}
                      >
                        {renderCell(cell)}
                      </TableCell>
                    );
                  })}
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
