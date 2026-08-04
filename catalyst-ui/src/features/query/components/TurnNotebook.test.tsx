import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QueryProfile, WorkbenchGenerationEvidence } from "../types";
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
  versionId: "11111111-1111-4111-8111-111111111111",
  ordinal: 1,
  authorType: "model" as const,
  queryDigest: "a".repeat(64),
  provenance: { model: "gemma-4-12b" },
};

const writerVersion = {
  versionId: "22222222-2222-4222-8222-222222222222",
  ordinal: 2,
  authorType: "model" as const,
  queryDigest: "b".repeat(64),
  provenance: { model: "gemma-4-12b", collaborationRole: "writer" },
};

const reviewerVersion = {
  versionId: "33333333-3333-4333-8333-333333333333",
  ordinal: 3,
  authorType: "model_repair" as const,
  queryDigest: "c".repeat(64),
  provenance: { model: "qwen2.5-14b", collaborationRole: "reviewer" },
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
  onShowEvidence: vi.fn(),
};

const diagnosticEvidence: WorkbenchGenerationEvidence = {
  contractVersion: "catalyst.workbench.generation-evidence.v1",
  evidenceId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  sessionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
  turnId: followupTurn.turnId,
  status: "failed",
  invocations: [],
  candidates: [
    {
      candidateId: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      attemptOrdinal: 2,
      role: "reviewer",
      candidateDigest: null,
      disposition: "diagnostic_only",
      versionRef: null,
      validationRef: null,
      rawEvidence: {
        available: true,
        inspectable: true,
        evidenceRef: "evidence://reviewer/attempt-2",
        payloadDigest: "d".repeat(64),
        contentType: "application/json",
        exactPayload: {
          error: "reviewer returned a contract-invalid correction",
          candidate: { sql: "SELECT FROM" },
        },
        omissionReason: null,
      },
    },
  ],
};

afterEach(() => {
  document.documentElement.style.fontSize = "";
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1024,
  });
});

describe("TurnNotebook", () => {
  it("shows the data-source badge on a turn that has one and omits it otherwise", () => {
    render(
      <TurnNotebook
        {...defaultProps}
        turns={[
          { ...initialTurn, dataSourceLabel: "OpenMRS HIV/ART program" },
          { ...followupTurn, dataSourceLabel: null },
        ]}
      />,
    );

    const withSource = screen.getByRole("button", { name: /query turn 1/i });
    expect(
      within(withSource).getByText("OpenMRS HIV/ART program"),
    ).toBeVisible();

    const withoutSource = screen.getByRole("button", { name: /query turn 2/i });
    expect(
      within(withoutSource).queryByText(/OpenMRS|OpenELIS/),
    ).not.toBeInTheDocument();
  });

  it("keeps every prior turn compact and read-only while one composer owns refinement", async () => {
    const user = userEvent.setup();
    render(<TurnNotebook {...defaultProps} />);

    const priorDisclosure = screen.getByRole("button", {
      name: /query turn 1/i,
    });
    const latestDisclosure = screen.getByRole("button", {
      name: /query turn 2/i,
    });
    expect(priorDisclosure).toHaveAttribute("aria-expanded", "false");
    expect(latestDisclosure).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(initialTurn.instruction)).toBeVisible();
    expect(screen.getByText(followupTurn.instruction)).toBeVisible();
    expect(
      screen.getAllByRole("textbox", { name: "Follow-up instruction" }),
    ).toHaveLength(1);

    await user.click(priorDisclosure);
    const priorTurn = screen.getByRole("region", { name: /query turn 1/i });
    expect(within(priorTurn).getByText(initialTurn.instruction)).toBeVisible();
    expect(within(priorTurn).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(priorTurn).queryByRole("button", { name: /generate/i }))
      .not.toBeInTheDocument();

    await user.click(latestDisclosure);
    expect(
      within(screen.getByRole("region", { name: /query turn 2/i }))
        .getByText(followupTurn.instruction),
    ).toBeVisible();
  });

  it("centers the follow-up composer on the exact base and names its author and models", () => {
    render(<TurnNotebook {...defaultProps} />);

    expect(
      screen.getByRole("heading", { name: "Refine Query v3" }),
    ).toBeVisible();
    expect(screen.getByText(/Based on Query v3/i)).toBeVisible();
    expect(screen.getByText(/reviewer correction/i)).toBeVisible();
    expect(screen.getByText(/qwen2\.5-14b/i)).toBeVisible();

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

  it("shows the selected reviewer correction separately from its superseded writer output", async () => {
    const user = userEvent.setup();
    render(<TurnNotebook {...defaultProps} />);

    await user.click(screen.getByRole("button", { name: /query turn 2/i }));
    const latestTurn = screen.getByRole("region", { name: /query turn 2/i });
    expect(
      within(latestTurn).getByText(/Query v3.*selected.*reviewer/i),
    ).toBeVisible();
    expect(
      within(latestTurn).getByText(/Query v2.*writer.*superseded/i),
    ).toBeVisible();
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

  it("minimizes to a compact grounding summary and restores the same composer", async () => {
    const user = userEvent.setup();
    render(<TurnNotebook {...defaultProps} />);

    expect(screen.getByText(/Execution summary: Query v3.*49 rows/i)).toBeVisible();
    const instruction = screen.getByRole("textbox", {
      name: "Follow-up instruction",
    });
    expect(instruction).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Minimize" }));
    expect(document.getElementById("refine-openelis")).toHaveAttribute(
      "data-minimized",
      "true",
    );
    expect(instruction.closest("form")).toHaveAttribute("hidden");
    expect(screen.getByRole("button", { name: "Expand" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    await user.click(screen.getByRole("button", { name: "Expand" }));
    expect(instruction.closest("form")).not.toHaveAttribute("hidden");
    expect(screen.getAllByRole("textbox", { name: "Follow-up instruction" }))
      .toHaveLength(1);
  });

  it.each([
    [
      "stale" as const,
      "Displayed results are stale for this editor. Run the current SQL to include a matching execution summary; result row values are not included.",
    ],
    [
      "not-executed" as const,
      "This query has not been executed. Refinement uses the current SQL without an execution summary or result row values.",
    ],
  ])("labels %s grounding without claiming row-value context", (kind, text) => {
    render(
      <TurnNotebook
        {...defaultProps}
        grounding={{ kind, text }}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("data-kind", kind);
    expect(status).toHaveTextContent(text);
  });

  it("retains a valid unselected writer on a failed turn without replacing the base", async () => {
    const user = userEvent.setup();
    const onShowEvidence = vi.fn();
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
        onShowEvidence={onShowEvidence}
      />,
    );

    await user.click(screen.getByRole("button", { name: /query turn 2/i }));
    expect(screen.getByText("Generation failed")).toBeVisible();
    expect(screen.getByText(/Reviewer did not return a response/)).toBeVisible();
    expect(screen.getByText(/Structured writer output.*Query v2.*not selected/i))
      .toBeVisible();
    expect(screen.getByText(/Based on Query v1/i)).toBeVisible();
    expect(screen.queryByText(/selected output.*Query v2/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "View generation evidence" }));
    expect(onShowEvidence).toHaveBeenCalledWith(failed.turnId);
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

  it("renders inspectable raw candidate evidence for a diagnostic-only failure", async () => {
    const user = userEvent.setup();
    render(
      <TurnNotebook
        {...defaultProps}
        evidence={diagnosticEvidence}
      />,
    );

    const rawEvidence = screen.getByText(
      /Reviewer attempt 2 raw evidence.*diagnostic only/i,
    );
    await user.click(rawEvidence);
    expect(
      screen.getByText(/reviewer returned a contract-invalid correction/i),
    ).toBeVisible();
    expect(screen.getByText(/SELECT FROM/i)).toBeVisible();
  });

  it.each(["timed_out", "cancelled"] as const)(
    "renders a %s model invocation outcome from recorded evidence",
    (outcome) => {
      render(
        <TurnNotebook
          {...defaultProps}
          evidence={{
            ...diagnosticEvidence,
            totalInvocationDurationMs: 250,
            invocations: [
              {
                invocationId: "99999999-9999-4999-8999-999999999999",
                role: "writer",
                stage: "followup_generation",
                attempt: 1,
                providerId: "llama.cpp",
                modelId: "gemma-4-12b",
                startedAt: "2026-07-18T12:00:00Z",
                endedAt: "2026-07-18T12:00:00Z",
                durationMs: 250,
                requestDigest: "9".repeat(64),
                responseDigest: null,
                failureDigest: "8".repeat(64),
                outcome,
              },
            ],
          }}
        />,
      );

      expect(screen.getByText("writer — gemma-4-12b")).toBeVisible();
      expect(
        screen.getByText(
          `followup_generation; attempt 1; ${outcome}; 250 ms`,
        ),
      ).toBeVisible();
    },
  );

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

    const composer = screen.getByRole("region", { name: "Refine Query v3" });
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
