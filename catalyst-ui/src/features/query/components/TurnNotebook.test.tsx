import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  QueryProfile,
  WorkbenchExecution,
  WorkbenchSession,
} from "../types";
import { TurnNotebook } from "./TurnNotebook";

type RevisionProfile = QueryProfile & { revisionCapable: boolean };

const profiles: RevisionProfile[] = [
  {
    id: "catalyst-query-gemma-4-12b",
    label: "Gemma writer + Qwen reviewer",
    available: true,
    revisionCapable: true,
    requiredModels: ["gemma-4-12b", "qwen2.5-14b"],
    roleModels: {
      query_generate: "gemma-4-12b",
      query_review: "qwen2.5-14b",
    },
    stages: ["query_generate", "query_lint", "query_review"],
    unavailableReasons: [],
  },
  {
    id: "catalyst-query-qwen-gemma-review",
    label: "Qwen writer + Gemma reviewer",
    available: true,
    revisionCapable: true,
    requiredModels: ["qwen2.5-14b", "gemma-4-12b"],
    roleModels: {
      query_generate: "qwen2.5-14b",
      query_review: "gemma-4-12b",
    },
    stages: ["query_generate", "query_lint", "query_review"],
    unavailableReasons: [],
  },
  {
    id: "catalyst-query-offline",
    label: "Offline split profile",
    available: false,
    revisionCapable: true,
    requiredModels: ["offline-writer", "offline-reviewer"],
    roleModels: {
      query_generate: "offline-writer",
      query_review: "offline-reviewer",
    },
    stages: ["query_generate", "query_review"],
    unavailableReasons: ["models are not loaded"],
  },
  {
    id: "catalyst-query-same-family",
    label: "Same-family research profile",
    available: true,
    revisionCapable: false,
    requiredModels: ["gemma-e4b"],
    roleModels: {
      query_generate: "gemma-e4b",
      query_review: "gemma-e4b",
    },
    stages: ["query_generate", "query_review"],
    unavailableReasons: [],
  },
];

const modelVersion = {
  expectedColumns: [],
  parameters: [],
  versionId: "11111111-1111-4111-8111-111111111111",
  ordinal: 1,
  authorType: "model" as const,
  queryDigest: "a".repeat(64),
  provenance: { model: "gemma-4-12b" },
  sql: "SELECT patient_id FROM analytics.lab_result_fact_v1",
};

const writerVersion = {
  expectedColumns: [],
  parameters: [],
  versionId: "22222222-2222-4222-8222-222222222222",
  ordinal: 2,
  authorType: "model" as const,
  queryDigest: "b".repeat(64),
  provenance: { model: "gemma-4-12b", collaborationRole: "writer" },
  sql: "SELECT patient_id, released FROM analytics.lab_result_fact_v1",
};

const reviewerVersion = {
  expectedColumns: [],
  parameters: [],
  versionId: "33333333-3333-4333-8333-333333333333",
  ordinal: 3,
  authorType: "model_repair" as const,
  queryDigest: "c".repeat(64),
  provenance: { model: "qwen2.5-14b", collaborationRole: "reviewer" },
  sql: "SELECT patient_id FROM analytics.lab_result_fact_v1 WHERE released",
};

// The notebook renders each cell's own recorded run, so it needs the session
// that owns the immutable versions, validations and executions.
const session = {
  contractVersion: "catalyst.workbench.session.v1",
  sessionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  question: "Show recent viral load results",
  profileId: profiles[0]!.id,
  datasetId: "openelis-analytics",
  datasetVersion: "lab_result_fact_v1",
  catalogVersion: "analytics-catalog-v1",
  currentVersionId: reviewerVersion.versionId,
  versions: [
    { ...modelVersion, parentVersionId: null },
    { ...writerVersion, parentVersionId: modelVersion.versionId },
    { ...reviewerVersion, parentVersionId: writerVersion.versionId },
  ],
  browserState: {},
  provenance: {},
  status: "active",
  createdAt: "2026-07-18T00:00:00Z",
  updatedAt: "2026-07-18T00:00:01Z",
  currentVersion: null,
  validations: [],
  latestValidation: null,
  executions: [],
} as unknown as WorkbenchSession;

const failedExecution: WorkbenchExecution = {
  contractVersion: "catalyst.workbench.execution.v1",
  queryDigest: reviewerVersion.queryDigest,
  idempotencyKey: "idem-2",
  validationStatus: "valid",
  query: { sql: reviewerVersion.sql, parameters: [] },
  statementTimeoutMs: 30000,
  maxRows: 1000,
  replayed: false,
  status: "failed",
  databaseDiagnostic: {
    sqlstate: "42703",
    severity: "ERROR",
    message: 'column "test_type" does not exist',
    detail: null,
    hint: null,
    position: 214,
  },
  durationMs: 18,
  executionId: "77777777-7777-4777-8777-777777777777",
  sessionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  versionId: reviewerVersion.versionId,
  ordinal: 2,
  completedAt: "2026-07-18T00:00:05Z",
};

const initialTurn = {
  turnId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  ordinal: 1,
  kind: "initial" as const,
  instruction: "Show recent viral load results",
  status: "completed" as const,
  selectedVersionId: modelVersion.versionId,
  outputVersions: [
    {
      selected: true,
      role: "writer" as const,
      contractValid: true,
      version: modelVersion,
    },
  ],
  profileSnapshot: {
    profileName: "Gemma writer + Qwen reviewer",
    writer: { modelId: "gemma-4-12b" },
    reviewer: { modelId: "qwen2.5-14b" },
  },
  failure: null,
};

const followupTurn = {
  turnId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  ordinal: 2,
  kind: "followup" as const,
  instruction: "Only include released results",
  status: "completed" as const,
  selectedVersionId: reviewerVersion.versionId,
  outputVersions: [
    {
      selected: false,
      role: "writer" as const,
      contractValid: true,
      version: writerVersion,
    },
    {
      selected: true,
      role: "reviewer" as const,
      contractValid: true,
      version: reviewerVersion,
    },
  ],
  profileSnapshot: initialTurn.profileSnapshot,
  failure: null,
};

const defaultProps = {
  turns: [initialTurn, followupTurn],
  session,
  baseVersion: reviewerVersion,
  instruction: "",
  profiles,
  selectedProfileId: profiles[0]!.id,
  grounding: {
    kind: "matching" as const,
    text: "Execution summary: Query v3 · Run 2 · 49 rows. Result row values are not included in model context.",
  },
  editorEmpty: false,
  busy: false,
  onInstructionChange: vi.fn(),
  onProfileChange: vi.fn(),
  onGenerate: vi.fn(),
  onOpenDetails: vi.fn(),
};

/**
 * Drive the scroll listener the composer's state machine reads. The handler
 * decides at most once per frame, so the frame has to be flushed.
 */
const scrollTo = async ({
  y,
  scrollHeight,
  innerHeight,
}: {
  y: number;
  scrollHeight: number;
  innerHeight: number;
}) => {
  Object.defineProperty(window, "scrollY", { configurable: true, value: y });
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: innerHeight,
  });
  await act(async () => {
    window.dispatchEvent(new Event("scroll"));
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
  });
};

afterEach(() => {
  document.documentElement.style.fontSize = "";
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
});

describe("TurnNotebook", () => {
  it("gives every turn a stable run counter and an addressable anchor", () => {
    render(<TurnNotebook {...defaultProps} />);

    const cells = document.querySelectorAll(".query-turn");
    expect(cells).toHaveLength(2);
    expect(document.getElementById("turn-1")).toBeInTheDocument();
    expect(document.getElementById("turn-2")).toBeInTheDocument();
    expect(screen.getByText("[1]")).toBeVisible();
    expect(screen.getByText("[2]")).toBeVisible();
    // The thread reads as ongoing, not finished.
    expect(screen.getByText("[3]")).toBeVisible();
    expect(screen.getByText("composing…")).toBeVisible();
  });

  it("opens only the newest turn and collapses earlier ones to their header", async () => {
    const user = userEvent.setup();
    render(<TurnNotebook {...defaultProps} />);

    const first = screen.getByRole("button", { name: /query turn 1/i });
    const latest = screen.getByRole("button", { name: /query turn 2/i });
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(latest).toHaveAttribute("aria-expanded", "true");

    // The instruction stays readable in the collapsed header, so the thread is
    // scannable without expanding anything.
    expect(within(first).getByText(initialTurn.instruction)).toBeVisible();
    expect(
      screen.queryByRole("region", { name: /query turn 1/i }),
    ).not.toBeInTheDocument();

    await user.click(first);
    const opened = screen.getByRole("region", { name: /query turn 1/i });
    expect(within(opened).getByText(modelVersion.sql)).toBeVisible();
    // Refinement stays with the one composer; a cell is never an editor.
    expect(within(opened).queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      within(opened).queryByRole("button", { name: /generate/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("textbox", { name: "Follow-up instruction" }),
    ).toHaveLength(1);

    await user.click(first);
    expect(first).toHaveAttribute("aria-expanded", "false");
  });

  it("carries the run outcome in the collapsed header and the status border", () => {
    render(
      <TurnNotebook
        {...defaultProps}
        turns={[
          { ...initialTurn, validationStatus: "valid" as const },
          { ...followupTurn, current: true, execution: failedExecution },
        ]}
      />,
    );

    expect(
      screen.getByRole("button", { name: /query turn 1.*not run/i }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /query turn 2.*run failed/i }),
    ).toBeVisible();
    expect(document.getElementById("turn-2")).toHaveAttribute(
      "data-status",
      "failed",
    );
    expect(document.getElementById("turn-2")).toHaveAttribute(
      "data-current",
      "true",
    );
  });

  it("centers the follow-up composer on the exact base and names its author and models", () => {
    render(<TurnNotebook {...defaultProps} />);

    expect(
      screen.getByRole("heading", { name: "Refine [2]" }),
    ).toBeVisible();
    expect(screen.getAllByText(/reviewer correction/i)[0]).toBeVisible();
    // "reviewer correction" now also labels the SQL block of the cell that
    // produced v3, so the composer's copy is one of several.
    expect(screen.getAllByText(/reviewer correction/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/qwen2\.5-14b/i).length).toBeGreaterThan(0);

    const selector = screen.getByRole("combobox", { name: "Model profile" });
    expect(
      within(selector).getByRole("option", {
        name: /Gemma writer \+ Qwen reviewer.*gemma-4-12b.*qwen2\.5-14b/i,
      }),
    ).toBeVisible();
  });

  it("labels an unresolved editor base without inventing Query v0", () => {
    render(
      <TurnNotebook
        {...defaultProps}
        baseVersion={null}
        editorState="unresolved"
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Refine unresolved editor" }),
    ).toBeVisible();
    expect(screen.getByText("Based on unresolved editor input")).toBeVisible();
    expect(screen.queryByText(/Query v0/i)).not.toBeInTheDocument();
    expect(document.getElementById("refine-openelis")).toHaveAttribute(
      "aria-labelledby",
      "refine-query-title",
    );
    expect(document.getElementById("catalyst-followup")).toBeInTheDocument();
  });

  it("shows the selected version's immutable SQL and names its author in the cell", () => {
    render(<TurnNotebook {...defaultProps} />);

    const latest = screen.getByRole("region", { name: /query turn 2/i });
    expect(within(latest).getByText(reviewerVersion.sql)).toBeVisible();
    expect(
      within(latest).getByText(/reviewer correction · qwen2\.5-14b/i),
    ).toBeVisible();
    // The writer candidate this turn superseded stays recorded, not hidden.
    expect(
      within(latest).getByText(/writer output — superseded/i),
    ).toBeVisible();
  });

  it("opens the details panel from the cell the detail belongs to", async () => {
    const user = userEvent.setup();
    const onOpenDetails = vi.fn();
    render(<TurnNotebook {...defaultProps} onOpenDetails={onOpenDetails} />);

    const latest = screen.getByRole("region", { name: /query turn 2/i });
    await user.click(within(latest).getByRole("button", { name: "details" }));
    expect(onOpenDetails).toHaveBeenCalledWith(followupTurn.turnId);

    // The diff link names the versions it compares and lands on Versions.
    await user.click(
      within(latest).getByRole("button", { name: "what changed" }),
    );
    expect(onOpenDetails).toHaveBeenLastCalledWith(
      followupTurn.turnId,
      "versions",
    );
  });

  it("generates one complete successor from a focused follow-up instruction", async () => {
    const user = userEvent.setup();
    const onGenerate = vi.fn();
    render(
      <TurnNotebook
        {...defaultProps}
        instruction="Narrow this to the last 30 days"
        onGenerate={onGenerate}
      />,
    );

    const instruction = screen.getByRole("textbox", {
      name: "Follow-up instruction",
    });
    instruction.focus();
    expect(instruction).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Generate next query" }));
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("collapses as you scroll up into history and returns as you scroll back", async () => {
    const user = userEvent.setup();
    render(<TurnNotebook {...defaultProps} />);

    const composerMode = () =>
      document.getElementById("refine-openelis")!.getAttribute("data-mode");
    const instruction = screen.getByRole("textbox", {
      name: "Follow-up instruction",
    });
    expect(composerMode()).toBe("full");
    expect(screen.getByText(/Execution summary: Query v3.*49 rows/i)).toBeVisible();

    // Land at the end of the thread, then scroll up into history: the
    // composer tucks to a lip and offers a way back rather than stranding you.
    await scrollTo({ y: 3200, scrollHeight: 4000, innerHeight: 800 });
    expect(composerMode()).toBe("full");
    await scrollTo({ y: 0, scrollHeight: 4000, innerHeight: 800 });
    expect(composerMode()).toBe("tucked");
    expect(instruction.closest("form")).toHaveAttribute("hidden");
    const jump = screen.getByRole("button", { name: /back to \[2\] · ask/ });
    expect(jump).toBeVisible();

    // Scrolling back down toward now brings it back in full.
    await scrollTo({ y: 3200, scrollHeight: 4000, innerHeight: 800 });
    expect(composerMode()).toBe("full");
    expect(instruction.closest("form")).not.toHaveAttribute("hidden");
    expect(screen.getAllByRole("textbox", { name: "Follow-up instruction" }))
      .toHaveLength(1);

    // Scrolling up while still near the end only drops it to one line, which
    // is the manual way back.
    await scrollTo({ y: 3100, scrollHeight: 4000, innerHeight: 800 });
    expect(composerMode()).toBe("line");
    await user.click(screen.getByRole("button", { name: /Refine \[2\]/ }));
    expect(composerMode()).toBe("full");
  });

  it("never hides the composer at a moment that would cost an action", async () => {
    // Typed text, a run in flight, and a failed last run each pin it open:
    // an action bar that disappears at the wrong moment costs more than the
    // space it saves.
    for (const props of [
      { instruction: "only released results" },
      { busy: true },
      { lastRunFailed: true },
    ]) {
      const view = render(<TurnNotebook {...defaultProps} {...props} />);
      await scrollTo({ y: 0, scrollHeight: 4000, innerHeight: 800 });
      expect(document.getElementById("refine-openelis")).toHaveAttribute(
        "data-mode",
        "full",
      );
      view.unmount();
    }
  });

  it("retains a valid unselected writer on a failed turn without replacing the base", async () => {
    const user = userEvent.setup();
    const onOpenDetails = vi.fn();
    const failed = {
      ...followupTurn,
      status: "failed" as const,
      selectedVersionId: null,
      outputVersions: [
        {
          selected: false,
          role: "writer" as const,
          contractValid: true,
          version: writerVersion,
        },
      ],
      failure: {
        stage: "reviewer_transport",
        code: "reviewer_transport_failed",
        message: "Reviewer did not return a response.",
      },
    };
    render(
      <TurnNotebook
        {...defaultProps}
        turns={[initialTurn, failed]}
        baseVersion={modelVersion}
        onOpenDetails={onOpenDetails}
      />,
    );

    expect(screen.getByText("Generation failed")).toBeVisible();
    expect(screen.getByText(/Reviewer did not return a response/)).toBeVisible();
    expect(screen.getByText(/Structured writer output.*not selected/i))
      .toBeVisible();
    
    expect(screen.queryByText(/selected output/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "details" }));
    expect(onOpenDetails).toHaveBeenCalledWith(failed.turnId);
  });

  it("offers only available different-family profiles and records per-turn switching", async () => {
    const user = userEvent.setup();
    const onProfileChange = vi.fn();
    render(
      <TurnNotebook
        {...defaultProps}
        onProfileChange={onProfileChange}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "Model profile" });
    expect(within(selector).getAllByRole("option")).toHaveLength(2);
    expect(within(selector).queryByText(/Offline split profile/i))
      .not.toBeInTheDocument();
    expect(within(selector).queryByText(/Same-family research profile/i))
      .not.toBeInTheDocument();

    await user.selectOptions(selector, profiles[1]!.id);
    expect(onProfileChange).toHaveBeenCalledWith(profiles[1]!.id);
  });

  it("explains and blocks generation when no revision-capable profile is available", async () => {
    const user = userEvent.setup();
    const onGenerate = vi.fn();
    render(
      <TurnNotebook
        {...defaultProps}
        profiles={profiles.filter((profile) => !profile.revisionCapable)}
        instruction="Narrow this query"
        onGenerate={onGenerate}
      />,
    );

    expect(
      screen.getByText(/No revision-capable model profile is currently available/i),
    ).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Model profile" })).toBeDisabled();
    const generate = screen.getByRole("button", { name: "Generate next query" });
    expect(generate).toBeDisabled();
    await user.click(generate);
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("disables refinement only for an empty editor, not unresolved nonempty input", () => {
    const { rerender } = render(
      <TurnNotebook {...defaultProps} editorEmpty editorState="empty" />,
    );
    expect(
      screen.getByRole("button", { name: "Generate next query" }),
    ).toBeDisabled();

    rerender(
      <TurnNotebook
        {...defaultProps}
        editorEmpty={false}
        editorState="unresolved"
      />,
    );
    expect(screen.getByText(/Unresolved editor input/i)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Generate next query" }),
    ).toBeEnabled();
  });

  it("supports keyboard disclosure, profile selection, instruction entry, and generation", async () => {
    const user = userEvent.setup();
    const onGenerate = vi.fn();
    const onInstructionChange = vi.fn();
    render(
      <TurnNotebook
        {...defaultProps}
        onGenerate={onGenerate}
        onInstructionChange={onInstructionChange}
      />,
    );

    const priorDisclosure = screen.getByRole("button", {
      name: /query turn 1/i,
    });
    priorDisclosure.focus();
    await user.keyboard("{Enter}");
    expect(priorDisclosure).toHaveAttribute("aria-expanded", "true");

    const selector = screen.getByRole("combobox", { name: "Model profile" });
    selector.focus();
    await user.keyboard("{ArrowDown}");
    expect(selector).toHaveFocus();

    const instruction = screen.getByRole("textbox", {
      name: "Follow-up instruction",
    });
    instruction.focus();
    await user.keyboard("Keep only final results");
    expect(onInstructionChange).toHaveBeenCalled();

    const generate = screen.getByRole("button", { name: "Generate next query" });
    generate.focus();
    await user.keyboard("{Enter}");
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("keeps one reachable composer and all actions grouped at narrow width and 200% text", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 320,
    });
    document.documentElement.style.fontSize = "200%";

    render(<TurnNotebook {...defaultProps} />);

    const composer = screen.getByRole("region", { name: "Refine [2]" });
    expect(composer).toBeVisible();
    expect(within(composer).getByRole("textbox", {
      name: "Follow-up instruction",
    })).toBeVisible();
    expect(within(composer).getByRole("combobox", {
      name: "Model profile",
    })).toBeVisible();
    expect(within(composer).getByRole("button", {
      name: "Generate next query",
    })).toBeVisible();
    expect(screen.getAllByRole("textbox", {
      name: "Follow-up instruction",
    })).toHaveLength(1);
  });
});
