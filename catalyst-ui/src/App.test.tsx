import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { CatalystApi } from "./features/query/api";
import {
  executionOutcome,
  policyOutcome,
  preview,
  queryOutcome,
  QUESTION,
  table,
} from "./features/query/test/fixtures";

const makeApi = (): CatalystApi => ({
  submitQuestion: vi.fn(),
  executePreview: vi.fn(),
  pollExecution: vi.fn(),
});

const askQuestion = async () => {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Question"), QUESTION);
  await user.click(screen.getByRole("button", { name: "Generate preview" }));
  return user;
};

describe("Catalyst query workflow", () => {
  it("keeps the demo boundary visible from the initial state", () => {
    render(<App api={makeApi()} />);

    expect(screen.getByText("Demo environment")).toBeVisible();
    expect(
      screen.getByText(/demo data only; not for clinical decision-making/i),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate preview" })).toBeDisabled();
  });

  it("submits a question and presents the authoritative preview", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    await askQuestion();

    expect(api.submitQuestion).toHaveBeenCalledWith(QUESTION);
    expect(await screen.findByRole("heading", { name: "Review query" })).toBeVisible();
    expect(screen.getByLabelText("Generated SQL")).toHaveTextContent(
      "SELECT collected_on, result_value FROM analytics.vw_viral_load_results WHERE result_value >= :minimum_result",
    );
    expect(screen.getByText("minimum_result")).toBeVisible();
    expect(screen.getByText("integer")).toBeVisible();
    expect(screen.getByText("80")).toBeVisible();
    expect(screen.getByRole("button", { name: "Accept and run" })).toBeEnabled();
    const trace = screen.getByLabelText("Reasoning trace");
    expect(within(trace).getAllByText("google/gemma-4-e4b")).toHaveLength(2);
    expect(within(trace).getAllByText("query review")).toHaveLength(2);
    expect(within(trace).getByText(/structured stage and validation summary/i)).toBeVisible();
  });

  it("shows the dataset browser and uses the Hub-owned available profile", async () => {
    const api = makeApi();
    api.getQueryOptions = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.query-options.v1",
      defaultProfileId: "catalyst-query-gemma-e4b",
      profiles: [
        {
          id: "catalyst-query-gemma-e4b",
          label: "Catalyst governed query — Gemma 4 E4B",
          available: true,
          requiredModels: ["google/gemma-4-e4b"],
          roleModels: {
            query_generate: "google/gemma-4-e4b",
            query_review: "google/gemma-4-e4b",
          },
          stages: ["context", "query_generate", "query_review", "query_finalize"],
          unavailableReasons: [],
        },
      ],
    });
    api.getDatasetOverview = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.dataset-overview.v1",
      datasetId: "catalyst-openelis-cohort-v1",
      synthetic: true,
      patients: 96,
      results: 1152,
      testTypes: 9,
      firstObservedAt: "2025-07-15T04:00:00Z",
      lastObservedAt: "2026-04-27T04:00:00Z",
      tests: [
        {
          testName: "Viral Load",
          unit: "copies/ml",
          results: 384,
          patients: 96,
          minimum: "30",
          median: "900",
          maximum: "35000",
        },
      ],
      exampleQuestions: ["Show viral load results since 2026-01-01"],
    });
    api.getDatasetRows = vi.fn().mockResolvedValue({
      contractVersion: "catalyst.dataset-rows.v1",
      total: 1,
      limit: 25,
      offset: 0,
      rows: [
        {
          patientId: "patient-123456789",
          testName: "Viral Load",
          value: "9000",
          unit: "copies/ml",
          observedAt: "2026-04-27T04:00:00Z",
          issuedAt: "2026-04-27T04:00:00Z",
          turnaroundMinutes: "120",
        },
      ],
    });
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    render(<App api={api} />);

    expect(await screen.findByText("1,152")).toBeVisible();
    expect(screen.getByText("9000 copies/ml")).toBeVisible();
    expect(screen.getByLabelText("Med-Agent Hub profile")).toHaveValue(
      "catalyst-query-gemma-e4b",
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Show viral load results since 2026-01-01" }));
    await user.click(screen.getByRole("button", { name: "Generate preview" }));
    expect(api.submitQuestion).toHaveBeenCalledWith(
      "Show viral load results since 2026-01-01",
      "catalyst-query-gemma-e4b",
    );
  });

  it("shows clarification without exposing an acceptance action", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(
      queryOutcome("needs_clarification"),
    );
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Clarification needed" }),
    ).toBeVisible();
    expect(screen.getByText("Which facility should be included?")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["unsupported", "Question unsupported"],
    ["rejected", "Question rejected"],
  ] as const)("shows a stable %s outcome", async (status, heading) => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(queryOutcome(status));
    render(<App api={api} />);

    await askQuestion();

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(screen.getByText(`The question was ${status}.`)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it("shows a rejected generated candidate and lint feedback without an execution action", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(queryOutcome("rejected"));
    render(<App api={api} />);

    await askQuestion();

    expect(await screen.findByText("Generated candidate")).toBeVisible();
    expect(screen.getByText("Not executable")).toBeVisible();
    expect(screen.getByLabelText("Rejected generated SQL")).toHaveTextContent(
      "result_value > 1000",
    );
    expect(
      screen.getByText("policy.unbound_predicate_literal"),
    ).toBeVisible();
    expect(screen.getByText(/Replace 1000 with a named parameter/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept and run" }),
    ).not.toBeInTheDocument();
  });

  it("distinguishes a Catalyst policy rejection and its violations", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(policyOutcome);
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Catalyst policy rejection" }),
    ).toBeVisible();
    expect(screen.getByText(policyOutcome.violations[0]!.message)).toBeVisible();
    expect(screen.getByText("Trace: cat-trace-policy")).toBeVisible();
  });

  it("accepts a preview and renders typed table data and provenance", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(table);
    render(<App api={api} />);

    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    const results = await screen.findByRole("region", { name: "Query results" });
    expect(within(results).getByText("1200")).toBeVisible();
    expect(within(results).getByText("450")).toBeVisible();
    expect(within(results).getByText("80")).toBeVisible();
    expect(within(results).getByText("result_value (copies/mL)")).toBeVisible();
    expect(api.executePreview).toHaveBeenCalledWith(
      preview,
      expect.any(String),
    );

    const provenance = screen.getByRole("region", { name: "Provenance" });
    expect(within(provenance).getByText("cat-trace-123")).toBeVisible();
    expect(within(provenance).getByText("hub-trace-456")).toBeVisible();
    expect(within(provenance).getByText("pipeline-run-77")).toBeVisible();
    expect(within(provenance).getByText("catalyst-query-gemma-e4b")).toBeVisible();
    expect(screen.getByText("Demo environment")).toBeVisible();
  });

  it("polls the accepted execution until the table is ready", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(
      executionOutcome("in_progress"),
    );
    let resolvePoll: (result: typeof table) => void = () => undefined;
    vi.mocked(api.pollExecution).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePoll = resolve;
        }),
    );
    render(<App api={api} pollIntervalMs={1} />);

    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Query running" }),
    ).toBeVisible();
    await waitFor(() => expect(api.pollExecution).toHaveBeenCalled());
    resolvePoll(table);
    expect(await screen.findByRole("region", { name: "Query results" })).toBeVisible();
    expect(api.pollExecution).toHaveBeenCalledWith(
      preview.previewId,
      expect.any(String),
      expect.any(AbortSignal),
    );
  });

  it.each([
    ["conflict", "Execution conflict"],
    ["failed", "Execution failed"],
  ] as const)("renders the terminal %s state", async (status, heading) => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    vi.mocked(api.executePreview).mockResolvedValue(executionOutcome(status));
    render(<App api={api} />);

    const user = await askQuestion();
    await user.click(
      await screen.findByRole("button", { name: "Accept and run" }),
    );

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(screen.getByText(`Execution ${status}.`)).toBeVisible();
    expect(screen.getByRole("button", { name: "Start a new query" })).toBeEnabled();
  });

  it("surfaces request failures and allows a retry", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockRejectedValue(
      new Error("Gateway is unavailable."),
    );
    render(<App api={api} />);

    await askQuestion();

    expect(
      await screen.findByRole("heading", { name: "Request failed" }),
    ).toBeVisible();
    expect(screen.getByText("Gateway is unavailable.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate preview" })).toBeEnabled();
  });

  it("trims a submitted question", async () => {
    const api = makeApi();
    vi.mocked(api.submitQuestion).mockResolvedValue(preview);
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.type(screen.getByLabelText("Question"), `  ${QUESTION}  `);
    await user.click(screen.getByRole("button", { name: "Generate preview" }));

    await waitFor(() => expect(api.submitQuestion).toHaveBeenCalledWith(QUESTION));
  });
});
