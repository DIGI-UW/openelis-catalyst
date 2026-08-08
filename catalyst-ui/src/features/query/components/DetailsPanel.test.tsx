import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  WorkbenchGenerationEvidence,
  WorkbenchQueryVersion,
  WorkbenchSession,
  WorkbenchValidation,
} from "../types";
import { DetailsPanel } from "./DetailsPanel";

const SQL = "SELECT count(*) FROM analytics.lab_result_fact_v1";

const version = (
  ordinal: number,
  overrides: Partial<WorkbenchQueryVersion> = {},
): WorkbenchQueryVersion => ({
  contractVersion: "catalyst.workbench.query-version.v1",
  versionId: `version-${ordinal}`,
  sessionId: "session-1",
  parentVersionId: ordinal === 1 ? null : `version-${ordinal - 1}`,
  ordinal,
  authorType: "model",
  sql: SQL,
  parameters: [],
  expectedColumns: [],
  queryDigest: `${ordinal}`.repeat(64),
  provenance: { model: "gemma-4-12b", profileId: "catalyst-query" },
  sourceFindingIds: [],
  repairProposalId: null,
  createdAt: "2026-08-07T10:00:00Z",
  ...overrides,
});

const validation: WorkbenchValidation = {
  contractVersion: "catalyst.workbench.validation.v1",
  queryDigest: "2".repeat(64),
  validatorRevision: "r14",
  validatorDigest: "d".repeat(64),
  status: "warning",
  advisory: true,
  checks: [{ name: "null_prone_column", status: "warned", findingIds: ["f-1"] }],
  findings: [
    {
      contractVersion: "catalyst.workbench.finding.v1",
      findingId: "f-1",
      ruleCode: "NULL_PRONE_COLUMN",
      severity: "warning",
      stage: "semantic",
      message: "result_date is null in 4% of rows; months may under-count.",
      path: "$.where.result_date",
      astUnit: null,
      span: null,
      evidence: null,
      suggestedAction: "Filter on collected_date instead.",
      repairability: "model",
      validatorRevision: "r14",
    },
  ],
  durationMs: 61,
  validationId: "validation-2",
  sessionId: "session-1",
  versionId: "version-2",
  ordinal: 2,
  createdAt: "2026-08-07T10:02:00Z",
};

const evidence = {
  contractVersion: "catalyst.workbench.generation-evidence.v1",
  evidenceId: "evidence-1",
  sessionId: "session-1",
  turnId: "turn-2",
  status: "completed",
  invocations: [
    {
      invocationId: "inv-1",
      role: "writer",
      modelId: "gemma-4-12b",
      stage: "generate",
      attempt: 1,
      outcome: "rejected",
      durationMs: 640,
      requestDigest: "a".repeat(64),
    },
    {
      invocationId: "inv-2",
      role: "reviewer",
      modelId: "qwen2.5-14b",
      stage: "repair",
      attempt: 1,
      outcome: "accepted",
      durationMs: 812,
      requestDigest: "b".repeat(64),
    },
  ],
  candidates: [
    {
      candidateId: "cand-1",
      role: "writer",
      attemptOrdinal: 1,
      disposition: "superseded",
      versionRef: null,
      rawEvidence: {
        inspectable: true,
        exactPayload: { sql: "SELECT bad FROM nowhere" },
      },
    },
  ],
  hubResponse: {
    inspectable: true,
    exactPayload: { turnId: "turn-2", totalInvocationDurationMs: 1452 },
  },
} as unknown as WorkbenchGenerationEvidence;

const session: WorkbenchSession = {
  contractVersion: "catalyst.workbench.session.v1",
  sessionId: "7f2a91c4-3b5e-4d21-9a0c-1f2e3d4c5b6a",
  question: "Monthly viral load, 2026",
  profileId: "catalyst-query",
  datasetId: "openelis-analytics",
  datasetVersion: "lab_result_fact_v1 · r7",
  catalogVersion: "analytics-catalog-v1",
  currentVersionId: "version-2",
  browserState: {},
  provenance: {},
  status: "active",
  createdAt: "2026-08-07T10:00:00Z",
  updatedAt: "2026-08-07T10:05:00Z",
  versions: [version(1), version(2)],
  currentVersion: version(2),
  validations: [validation],
  latestValidation: validation,
  executions: [],
};

const defaultProps = {
  session,
  turnOrdinal: 2,
  version: version(2),
  validation,
  evidence: null,
  evidenceLoading: false,
  evidenceError: null,
  tab: "validation" as const,
  developerMode: false,
  stacked: false,
  railWidth: 240,
  onTabChange: vi.fn(),
  onDeveloperModeChange: vi.fn(),
  onClose: vi.fn(),
};

describe("DetailsPanel", () => {
  it("names the cell it is scoped to and offers the four detail tiers", () => {
    render(<DetailsPanel {...defaultProps} />);

    const panel = screen.getByRole("complementary", { name: "Details" });
    expect(within(panel).getByText("Turn [2] · Query v2")).toBeVisible();
    for (const name of ["Validation", "Evidence", "Provenance", "Versions"]) {
      expect(within(panel).getByRole("tab", { name })).toBeVisible();
    }
    expect(screen.getByRole("tab", { name: "Validation" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("keeps validation advisory and shows each finding with its guidance", () => {
    render(<DetailsPanel {...defaultProps} />);

    expect(
      screen.getByText(/Validation is advisory\..*never prevent a run/),
    ).toBeVisible();
    expect(screen.getByText("NULL_PRONE_COLUMN")).toBeVisible();
    expect(screen.getByText("warning")).toBeVisible();
    expect(screen.getByText("$.where.result_date")).toBeVisible();
    expect(
      screen.getByText("Suggested action: Filter on collected_date instead."),
    ).toBeVisible();
    expect(screen.getByText("validator r14 · 61 ms")).toBeVisible();
  });

  it("hides identifiers until developer mode asks for them", async () => {
    const user = userEvent.setup();
    const onDeveloperModeChange = vi.fn();
    const { rerender } = render(
      <DetailsPanel {...defaultProps} tab="evidence" evidence={evidence} />,
    );

    expect(screen.queryByText(/^request /)).not.toBeInTheDocument();
    await user.click(
      screen.getByLabelText(/Developer mode/, { exact: false }),
    );
    expect(onDeveloperModeChange).not.toHaveBeenCalled();

    rerender(
      <DetailsPanel
        {...defaultProps}
        tab="evidence"
        evidence={evidence}
        developerMode
        onDeveloperModeChange={onDeveloperModeChange}
      />,
    );
    expect(screen.getByText(/^request aaaa…aaaa$/)).toBeVisible();
  });

  it("reports every model invocation and keeps the exact payloads one level down", () => {
    render(
      <DetailsPanel {...defaultProps} tab="evidence" evidence={evidence} />,
    );

    expect(
      screen.getByText(/does not expose hidden reasoning/),
    ).toBeVisible();
    expect(screen.getByText("writer")).toBeVisible();
    expect(screen.getByText("gemma-4-12b")).toBeVisible();
    expect(screen.getByText("rejected")).toBeVisible();
    expect(screen.getByText("generate · attempt 1 · 640 ms")).toBeVisible();
    expect(screen.getByText("reviewer")).toBeVisible();
    expect(screen.getByText("accepted")).toBeVisible();

    // Tier 3: recorded payloads are present but collapsed.
    const disclosure = screen
      .getByText(/Writer candidate, attempt 1 — superseded/)
      .closest("details");
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByText("Recorded Hub response")).toBeVisible();
  });

  it("reports an empty evidence tab truthfully rather than silently", () => {
    render(<DetailsPanel {...defaultProps} tab="evidence" />);
    expect(
      screen.getByText("No generation evidence was recorded for this turn."),
    ).toBeVisible();

    render(
      <DetailsPanel
        {...defaultProps}
        tab="evidence"
        evidenceError="Generation evidence is unavailable."
      />,
    );
    expect(screen.getAllByRole("alert")[0]).toHaveTextContent(
      "Generation evidence is unavailable.",
    );
  });

  it("grounds provenance in the scoped version, not the session's latest", () => {
    render(
      <DetailsPanel {...defaultProps} tab="provenance" version={version(1)} />,
    );

    expect(screen.getByText("Version").closest("div")).toHaveTextContent("v1");
    expect(screen.getByText("Query digest").closest("div")).toHaveTextContent(
      "1111…1111",
    );
    expect(screen.getByText("Catalog").closest("div")).toHaveTextContent(
      "analytics-catalog-v1",
    );
    expect(
      screen.getByText("Dataset version").closest("div"),
    ).toHaveTextContent("lab_result_fact_v1 · r7");
  });

  it("lists every version newest first and marks the current one", () => {
    render(<DetailsPanel {...defaultProps} tab="versions" />);

    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent?.slice(0, 9))).toEqual([
      "Version 2",
      "Version 1",
    ]);
    expect(within(items[0]!).getByText("Current")).toBeVisible();
    expect(items[0]).toHaveAttribute("data-current", "true");
    expect(items[1]).not.toHaveAttribute("data-current");
  });

  it("closes on the close button, the scrim and Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<DetailsPanel {...defaultProps} onClose={onClose} />);

    // Focus lands in the panel so a keyboard user is not stranded behind it.
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Close details" }));
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
