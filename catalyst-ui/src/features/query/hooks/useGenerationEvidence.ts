import { useState } from "react";
import type { CatalystApi } from "../api";
import type { WorkbenchGenerationEvidence } from "../types";

const messageFromError = (error: unknown) =>
  error instanceof Error ? error.message : "An unexpected request error occurred.";

/**
 * The generation-evidence panel's state: which turn's evidence is loaded,
 * which is loading, and what went wrong.
 *
 * One of five hooks extracted from QueryWorkspace, which had grown to 37
 * pieces of state in one closure. Each hook owns one measured cluster; the
 * component composes them.
 */
export const useGenerationEvidence = (api: CatalystApi) => {
  const [evidence, setEvidence] = useState<WorkbenchGenerationEvidence | null>(
    null,
  );
  const [loadingTurnId, setLoadingTurnId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const show = async (sessionId: string | null, turnId: string) => {
    if (!sessionId || !api.getWorkbenchGenerationEvidence) {
      setError("Generation evidence is unavailable.");
      return;
    }
    setLoadingTurnId(turnId);
    setError(null);
    try {
      setEvidence(await api.getWorkbenchGenerationEvidence(sessionId, turnId));
    } catch (requestError) {
      setError(messageFromError(requestError));
    } finally {
      setLoadingTurnId(null);
    }
  };

  /** Everything gone — a new session starts with no evidence at all. */
  const reset = () => {
    setEvidence(null);
    setLoadingTurnId(null);
    setError(null);
  };

  /**
   * A regeneration makes the shown evidence describe the wrong turn. The
   * loading marker is deliberately left alone: it belongs to an in-flight
   * request, not to the display being invalidated.
   */
  const invalidate = () => {
    setEvidence(null);
    setError(null);
  };

  return { evidence, loadingTurnId, error, show, reset, invalidate };
};
