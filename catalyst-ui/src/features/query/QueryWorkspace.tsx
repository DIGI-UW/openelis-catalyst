import { Renew } from "@carbon/icons-react";
import { Button } from "@carbon/react";
import { useEffect, useState } from "react";
import type { CatalystApi } from "./api";
import { catalystApi } from "./api";
import { ExecutionState } from "./components/ExecutionState";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { QueryPreview } from "./components/QueryPreview";
import { QuestionForm } from "./components/QuestionForm";
import { ResultsTable } from "./components/ResultsTable";
import {
  isPreview,
  isTable,
  type CatalystExecutionOutcome,
  type CatalystPolicyOutcome,
  type CatalystPreview,
  type CatalystQueryOutcome,
  type CatalystTable,
} from "./types";

type WorkflowState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "preview"; preview: CatalystPreview; executing: boolean }
  | { kind: "query-outcome"; outcome: CatalystQueryOutcome }
  | { kind: "policy-outcome"; outcome: CatalystPolicyOutcome }
  | {
      kind: "polling";
      preview: CatalystPreview;
      idempotencyKey: string;
      outcome: CatalystExecutionOutcome;
    }
  | { kind: "execution-outcome"; outcome: CatalystExecutionOutcome }
  | { kind: "result"; result: CatalystTable }
  | { kind: "error"; message: string };

interface QueryWorkspaceProps {
  api?: CatalystApi;
  pollIntervalMs?: number;
}

const messageFromError = (error: unknown) =>
  error instanceof Error ? error.message : "An unexpected request error occurred.";

const createIdempotencyKey = () => {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `catalyst-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const executionHeading = (outcome: CatalystExecutionOutcome) =>
  ({
    in_progress: "Query running",
    not_found: "Execution not found",
    expired: "Preview expired",
    conflict: "Execution conflict",
    failed: "Execution failed",
  })[outcome.status];

const executionKind = (
  outcome: CatalystExecutionOutcome,
): "info" | "warning" | "error" => {
  if (outcome.status === "in_progress") return "info";
  if (outcome.status === "expired" || outcome.status === "conflict") {
    return "warning";
  }
  return "error";
};

const QueryOutcomeState = ({ outcome }: { outcome: CatalystQueryOutcome }) => {
  const content = {
    needs_clarification: {
      title: "Clarification needed",
      message: outcome.clarification ?? "Please clarify the question.",
      kind: "info" as const,
    },
    unsupported: {
      title: "Question unsupported",
      message: outcome.message ?? "This question is not supported.",
      kind: "warning" as const,
    },
    rejected: {
      title: "Question rejected",
      message: outcome.message ?? "The query request was rejected.",
      kind: "error" as const,
    },
  }[outcome.status];

  return (
    <ExecutionState
      title={content.title}
      message={content.message}
      kind={content.kind}
      details={
        <p className="trace-line">
          Hub trace: <span>{outcome.provenance.traceId}</span>
        </p>
      }
    />
  );
};

export const QueryWorkspace = ({
  api = catalystApi,
  pollIntervalMs = 1000,
}: QueryWorkspaceProps) => {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<WorkflowState>({ kind: "idle" });

  useEffect(() => {
    if (state.kind !== "polling") return;

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await api.pollExecution(
          state.preview.previewId,
          state.idempotencyKey,
          controller.signal,
        );
        if (isTable(response)) {
          setState({ kind: "result", result: response });
        } else if (response.status === "in_progress") {
          setState({ ...state, outcome: response });
        } else {
          setState({ kind: "execution-outcome", outcome: response });
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setState({ kind: "error", message: messageFromError(error) });
        }
      }
    }, pollIntervalMs);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, pollIntervalMs, state]);

  const submitQuestion = async (normalizedQuestion: string) => {
    setState({ kind: "submitting" });
    try {
      const response = await api.submitQuestion(normalizedQuestion);
      if (isPreview(response)) {
        setState({ kind: "preview", preview: response, executing: false });
      } else if (response.contractVersion === "catalyst.query.v1") {
        setState({ kind: "query-outcome", outcome: response });
      } else {
        setState({ kind: "policy-outcome", outcome: response });
      }
    } catch (error) {
      setState({ kind: "error", message: messageFromError(error) });
    }
  };

  const acceptPreview = async () => {
    if (state.kind !== "preview" || state.executing) return;
    const preview = state.preview;
    const idempotencyKey = createIdempotencyKey();
    setState({ kind: "preview", preview, executing: true });
    try {
      const response = await api.executePreview(preview, idempotencyKey);
      if (isTable(response)) {
        setState({ kind: "result", result: response });
      } else if (response.status === "in_progress") {
        setState({
          kind: "polling",
          preview,
          idempotencyKey,
          outcome: response,
        });
      } else {
        setState({ kind: "execution-outcome", outcome: response });
      }
    } catch (error) {
      setState({ kind: "error", message: messageFromError(error) });
    }
  };

  const reset = () => {
    setQuestion("");
    setState({ kind: "idle" });
  };

  const questionIsLocked =
    state.kind === "preview" ||
    state.kind === "polling" ||
    state.kind === "execution-outcome" ||
    state.kind === "result";

  return (
    <main className="app-shell">
      <div className="app-shell__intro">
        <p className="product-mark">OpenELIS Global / Catalyst</p>
        <p>Governed query review and typed table results</p>
      </div>

      <QuestionForm
        question={question}
        busy={state.kind === "submitting"}
        disabled={questionIsLocked}
        onQuestionChange={setQuestion}
        onSubmit={submitQuestion}
      />

      {state.kind === "submitting" && (
        <ExecutionState
          title="Preparing preview"
          message="Catalyst is validating the question and proposed query."
          running
        />
      )}

      {state.kind === "preview" && (
        <QueryPreview
          preview={state.preview}
          executing={state.executing}
          onAccept={acceptPreview}
        />
      )}

      {state.kind === "query-outcome" && (
        <QueryOutcomeState outcome={state.outcome} />
      )}

      {state.kind === "policy-outcome" && (
        <ExecutionState
          title="Catalyst policy rejection"
          message={state.outcome.message}
          kind="error"
          details={
            <div className="outcome-details">
              <ul>
                {state.outcome.violations.map((violation) => (
                  <li key={`${violation.code}-${violation.message}`}>
                    {violation.message}
                  </li>
                ))}
              </ul>
              <p>Trace: {state.outcome.catalystTraceId}</p>
            </div>
          }
        />
      )}

      {state.kind === "polling" && (
        <ExecutionState
          title="Query running"
          message={state.outcome.message}
          kind="info"
          running
        />
      )}

      {state.kind === "execution-outcome" && (
        <ExecutionState
          title={executionHeading(state.outcome)}
          message={state.outcome.message}
          kind={executionKind(state.outcome)}
          actionLabel="Start a new query"
          onAction={reset}
        />
      )}

      {state.kind === "result" && (
        <>
          <ResultsTable result={state.result} />
          <ProvenancePanel result={state.result} />
          <div className="new-query-action">
            <Button kind="tertiary" renderIcon={Renew} onClick={reset}>
              Start a new query
            </Button>
          </div>
        </>
      )}

      {state.kind === "error" && (
        <ExecutionState
          title="Request failed"
          message={state.message}
          kind="error"
        />
      )}
    </main>
  );
};
