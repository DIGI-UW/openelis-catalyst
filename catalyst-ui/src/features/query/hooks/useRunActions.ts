import { useState } from "react";

/**
 * What is in flight and what it last said: the run/generation busy flags,
 * the workbench error line, the announcement for assistive tech, and the
 * follow-up instruction waiting to be sent.
 *
 * One of five hooks extracted from QueryWorkspace. The workflows that set
 * these (run, generate, submit) cross every cluster and remain in the
 * component; this hook owns the state they report through.
 */
export const useRunActions = () => {
  const [workbenchBusy, setWorkbenchBusy] = useState<"running" | null>(null);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);
  const [workbenchAnnouncement, setWorkbenchAnnouncement] = useState("");
  const [followupInstruction, setFollowupInstruction] = useState("");
  const [followupBusy, setFollowupBusy] = useState(false);
  /** A follow-up that never became a turn, so no cell can report it. */
  const [followupError, setFollowupError] = useState<string | null>(null);

  return {
    workbenchBusy,
    setWorkbenchBusy,
    workbenchError,
    setWorkbenchError,
    workbenchAnnouncement,
    setWorkbenchAnnouncement,
    followupInstruction,
    setFollowupInstruction,
    followupBusy,
    setFollowupBusy,
    followupError,
    setFollowupError,
  };
};
