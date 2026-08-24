import { useState } from "react";
import { Button, Tag } from "@carbon/react";

import type { WorkbenchGuidanceEntry } from "../types";

interface SessionGuidanceProps {
  entries: WorkbenchGuidanceEntry[];
  busy: boolean;
  onPin(text: string): void;
  onUnpin(entryId: string): void;
}

/**
 * Standing instructions for this session, verbatim.
 *
 * Whatever is pinned here rides every later generation without being
 * repeated in the follow-up box; the current instruction still outranks it.
 */
export function SessionGuidance({
  entries,
  busy,
  onPin,
  onUnpin,
}: SessionGuidanceProps) {
  const [draft, setDraft] = useState("");
  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onPin(text);
    setDraft("");
  };
  return (
    <div className="session-guidance" aria-label="Session guidance">
      <div className="session-guidance__entries">
        {entries.map((entry) => (
          <Tag key={entry.entryId} type="cool-gray">
            <span>{entry.text}</span>
            <button
              type="button"
              aria-label={`Unpin: ${entry.text}`}
              disabled={busy}
              onClick={() => onUnpin(entry.entryId)}
            >
              ✕
            </button>
          </Tag>
        ))}
      </div>
      <div className="session-guidance__composer">
        <label className="visually-hidden" htmlFor="session-guidance-input">
          Pin session guidance
        </label>
        <input
          id="session-guidance-input"
          type="text"
          value={draft}
          placeholder="Pin guidance for every later turn…"
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
        />
        <Button size="sm" kind="tertiary" disabled={busy} onClick={submit}>
          Pin
        </Button>
      </div>
    </div>
  );
}
