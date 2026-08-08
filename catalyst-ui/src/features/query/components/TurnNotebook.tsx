import { Button, Tag } from "@carbon/react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import type {
  QueryProfile,
  WorkbenchExecution,
  WorkbenchQueryVersion,
  WorkbenchSession,
} from "../types";
import type { DetailsTab } from "./DetailsPanel";
import { ExecutionResult } from "./WorkbenchPanel";
import "./TurnNotebook.css";

type NotebookVersion = Pick<
  WorkbenchQueryVersion,
  "versionId" | "ordinal" | "authorType" | "queryDigest" | "provenance" | "sql"
>;

export interface NotebookOutputVersion {
  selected: boolean;
  role: "writer" | "reviewer";
  contractValid: boolean;
  version?: NotebookVersion;
  versionId?: string;
}

export interface NotebookTurn {
  turnId: string;
  ordinal: number;
  kind: "initial" | "followup";
  instruction: string;
  status: "requested" | "completed" | "failed";
  selectedVersionId: string | null;
  outputVersions: NotebookOutputVersion[];
  profileSnapshot: {
    profileName: string | null;
    writer: { modelId: string } | null;
    reviewer: { modelId: string } | null;
  };
  failure: { message: string } | null;
  /** Run recorded against this turn's selected version, if it has been run. */
  execution?: WorkbenchExecution | null;
  /** Advisory validation status for this turn's selected version. */
  validationStatus?: "invalid" | "warning" | "valid" | null;
  /** True for the turn whose selected version is the session's current one. */
  current?: boolean;
}

export interface NotebookGrounding {
  kind: "matching" | "stale" | "not-executed";
  text: string;
}

interface TurnNotebookProps {
  turns: NotebookTurn[];
  session: WorkbenchSession;
  baseVersion: NotebookVersion | null;
  instruction: string;
  profiles: QueryProfile[];
  selectedProfileId: string;
  grounding: NotebookGrounding;
  editorEmpty: boolean;
  editorState?: "ready" | "empty" | "unresolved";
  busy: boolean;
  generating?: boolean;
  /** The last run failed, which pins the composer open on its error state. */
  lastRunFailed?: boolean;
  onInstructionChange: (instruction: string) => void;
  onProfileChange: (profileId: string) => void;
  onGenerate: () => void;
  /** Open the Details panel scoped to this turn, on a chosen tab. */
  onOpenDetails: (turnId: string, tab?: DetailsTab) => void;
  /**
   * The editable current query, rendered as the last cell in the stack: the
   * work in progress sits where the next committed turn will, rather than in
   * a panel detached from the thread it belongs to.
   */
  activeCell?: ReactNode;
}

const textAt = (source: Record<string, unknown>, key: string) => {
  const value = source[key];
  return typeof value === "string" && value ? value : null;
};

const profileOptionLabel = (profile: QueryProfile) => {
  const writer = profile.roleModels.query_generate;
  const reviewer = profile.roleModels.query_review;
  const models = [
    writer ? `writer ${writer}` : null,
    reviewer ? `reviewer ${reviewer}` : null,
  ].filter((value): value is string => value !== null);
  return models.length > 0
    ? `${profile.label} — ${models.join("; ")}`
    : profile.label;
};

const versionAuthor = (version: NotebookVersion | null) => {
  if (!version) return "unresolved editor input";
  const collaborationRole = textAt(version.provenance, "collaborationRole");
  if (version.authorType === "model_repair" || collaborationRole === "reviewer") {
    return "reviewer correction";
  }
  if (version.authorType === "model") return "model writer";
  return version.authorType.replaceAll("_", " ");
};

const versionModel = (version: NotebookVersion | null) =>
  version ? textAt(version.provenance, "model") : null;

const selectedVersionOf = (turn: NotebookTurn): NotebookVersion | null =>
  turn.outputVersions.find((output) => output.selected)?.version ??
  turn.outputVersions.find(
    (output) => output.versionId === turn.selectedVersionId,
  )?.version ??
  null;

/**
 * Status is what the analyst reads from the gutter at a glance: whether this
 * turn produced a query that ran, and whether that run came back clean. A turn
 * whose generation failed never reaches a run, so it reports the same red as a
 * failed run rather than an absent one.
 */
type CellStatus = "succeeded" | "failed" | "not-run";

const cellStatus = (turn: NotebookTurn): CellStatus => {
  if (turn.status === "failed") return "failed";
  if (turn.execution?.status === "failed") return "failed";
  if (turn.execution?.status === "succeeded") return "succeeded";
  return "not-run";
};

const rowLabel = (count: number) => `${count} ${count === 1 ? "row" : "rows"}`;

/** The right-hand summary in a collapsed header: `v3 · 12 rows`. */
const cellOutcome = (turn: NotebookTurn) => {
  if (turn.status === "failed") return "generation failed";
  if (turn.status === "requested") return "generating…";
  const execution = turn.execution;
  if (!execution) return "not run";
  if (execution.status === "failed") return "run failed";
  const returned = execution.result?.rowCount.returned;
  return returned === undefined ? "ran" : rowLabel(returned);
};

const validationWord = (status: NotebookTurn["validationStatus"]) => {
  if (status === "valid") return "✓ valid";
  if (status === "warning") return "checked with warnings";
  if (status === "invalid") return "findings raised";
  return null;
};

export const TurnNotebook = ({
  turns,
  session,
  baseVersion,
  instruction,
  profiles,
  selectedProfileId,
  grounding,
  editorEmpty,
  editorState = editorEmpty ? "empty" : "ready",
  busy,
  generating = false,
  lastRunFailed = false,
  onInstructionChange,
  onProfileChange,
  onGenerate,
  onOpenDetails,
  activeCell = null,
}: TurnNotebookProps) => {
  const [turnVisibilityOverrides, setTurnVisibilityOverrides] = useState<
    Record<string, boolean>
  >({});
  // Borrowed from a browser's URL bar with the direction inverted: the newest
  // turn is at the bottom, so scrolling up into history hides the composer and
  // scrolling back down toward now returns it.
  const [scrollMode, setScrollMode] = useState<"full" | "line" | "tucked">(
    "full",
  );
  const [composerFocused, setComposerFocused] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // An action bar that disappears at the wrong moment costs more than the
  // space it saves, so these states hold it open regardless of scrolling.
  const composerPinned =
    instruction.trim().length > 0 ||
    composerFocused ||
    busy ||
    lastRunFailed;

  useEffect(() => {
    if (composerPinned) return;

    // Two things caused flicker before: the mode was recomputed from the sign
    // of every individual scroll event, so any jitter near a threshold flipped
    // it; and the thresholds were single values, so hovering on one oscillated.
    // Intent is now accumulated until it is unambiguous, each state has to be
    // clearly left before it changes, and at most one decision is made per
    // frame.
    const INTENT = 24; // px of consistent travel before the mode may change
    const NEAR_END = 200; // scrolling down within this: full
    const LEAVE_END = 320; // once full, only leave beyond this
    const FAR_BACK = 560; // scrolling up beyond this: tucked
    const LEAVE_FAR = 440; // once tucked, only leave inside this

    let lastY = window.scrollY;
    let intent = 0;
    let frame = 0;

    const measure = () => {
      frame = 0;
      const y = window.scrollY;
      const delta = y - lastY;
      lastY = y;
      // Reverse of travel abandons the intent that was building.
      intent = Math.sign(intent) === Math.sign(delta) ? intent + delta : delta;
      if (Math.abs(intent) < INTENT) return;

      const gap =
        document.documentElement.scrollHeight - (y + window.innerHeight);

      // Read out of the mutable accumulator before handing React an updater:
      // the updater runs later, by which time `intent` has been reset.
      const towardNow = intent > 0;
      intent = 0;

      setScrollMode((current) => {
        if (towardNow) {
          if (gap < NEAR_END) return "full";
          return current === "full" && gap < LEAVE_END ? "full" : "line";
        }
        if (gap > FAR_BACK) return "tucked";
        return current === "tucked" && gap > LEAVE_FAR ? "tucked" : "line";
      });
    };

    const onScroll = () => {
      if (frame === 0) frame = window.requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [composerPinned]);

  const composerMode = composerPinned ? "full" : scrollMode;

  const restoreComposer = () => {
    setScrollMode("full");
    window.requestAnimationFrame(() => composerRef.current?.focus());
  };
  const revisionProfiles = useMemo(
    () => profiles.filter(
      (profile) => profile.available && profile.revisionCapable === true,
    ),
    [profiles],
  );
  const noRevisionProfiles = revisionProfiles.length === 0;
  const composerTitle = lastRunFailed && baseVersion
    ? `Query v${baseVersion.ordinal} failed`
    : baseVersion
      ? `Refine Query v${baseVersion.ordinal}`
      : "Refine unresolved editor";
  const latestTurnId = turns.at(-1)?.turnId ?? null;

  const toggleTurn = (turnId: string, expanded: boolean) => {
    setTurnVisibilityOverrides((current) => ({
      ...current,
      [turnId]: !expanded,
    }));
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (editorEmpty || busy || noRevisionProfiles) return;
    onGenerate();
  };

  const renderCell = (turn: NotebookTurn) => {
    // Only the newest turn is open on arrival; everything earlier collapses to
    // its header so the thread stays scannable. An explicit toggle always wins.
    const expanded =
      turnVisibilityOverrides[turn.turnId] ?? turn.turnId === latestTurnId;
    const regionId = `query-turn-${turn.turnId}`;
    const status = cellStatus(turn);
    const version = selectedVersionOf(turn);
    const execution = turn.execution ?? null;
    const validation = validationWord(turn.validationStatus);
    const outcome = cellOutcome(turn);
    // The version this one succeeded, so the footer can offer the comparison
    // the analyst actually wants: what this turn changed.
    const previousVersionOrdinal = (() => {
      const parentId = version
        ? session.versions.find((item) => item.versionId === version.versionId)
            ?.parentVersionId
        : null;
      if (!parentId) return null;
      return (
        session.versions.find((item) => item.versionId === parentId)?.ordinal ??
        null
      );
    })();
    // The visible header truncates the instruction to one line, so the
    // accessible name carries it in full alongside the run counter it is cited
    // by and the outcome the status dot encodes visually.
    const headerLabel =
      `Query turn ${turn.ordinal}: ${turn.instruction} — ` +
      `${version ? `Query v${version.ordinal}, ` : ""}${outcome}`;

    return (
      <article
        className="query-turn"
        id={`turn-${turn.ordinal}`}
        key={turn.turnId}
        data-status={status}
        data-current={turn.current ? "true" : undefined}
      >
        <div className="query-turn__gutter" aria-hidden="true">
          [{turn.ordinal}]
        </div>
        <div className="query-turn__body">
          <button
            type="button"
            className="query-turn__disclosure"
            aria-expanded={expanded}
            aria-controls={regionId}
            aria-label={headerLabel}
            onClick={() => toggleTurn(turn.turnId, expanded)}
          >
            <span className="query-turn__dot" aria-hidden="true" />
            <span className="query-turn__summary">{turn.instruction}</span>
            <span className="query-turn__outcome">
              {version ? `v${version.ordinal} · ` : ""}
              {outcome}
            </span>
            <span className="query-turn__caret" aria-hidden="true">
              {expanded ? "▾" : "▸"}
            </span>
          </button>

          {expanded && (
            <section
              id={regionId}
              className="query-turn__detail"
              aria-label={`Query turn ${turn.ordinal}`}
            >
              {version && (
                <div
                  className="query-turn__sql"
                  data-author={
                    version.authorType === "human" ? "human" : undefined
                  }
                >
                  <p className="query-turn__sql-label">
                    Query v{version.ordinal} · {versionAuthor(version)}
                    {versionModel(version) ? ` · ${versionModel(version)}` : ""}
                  </p>
                  <pre>{version.sql}</pre>
                </div>
              )}

              {turn.profileSnapshot.profileName && (
                <p className="query-turn__profile">
                  Generated by {turn.profileSnapshot.profileName}
                  {turn.profileSnapshot.writer
                    ? ` · ${turn.profileSnapshot.writer.modelId} writer`
                    : ""}
                  {turn.profileSnapshot.reviewer
                    ? ` · ${turn.profileSnapshot.reviewer.modelId} reviewer`
                    : ""}
                </p>
              )}

              {turn.outputVersions
                .filter((output) => !output.selected && output.version)
                .map((output, index) => (
                  <p
                    className="query-turn__superseded"
                    key={`${output.version!.versionId}-${index}`}
                  >
                    {turn.status === "failed" && output.role === "writer"
                      ? `Structured writer output — Query v${output.version!.ordinal} — not selected`
                      : `Query v${output.version!.ordinal} — ${output.role} output — superseded`}
                  </p>
                ))}

              {turn.status === "failed" && (
                <div className="query-turn__failure" role="status">
                  <strong>Generation failed</strong>
                  <p>
                    {turn.failure?.message ?? "The generation did not complete."}
                  </p>
                </div>
              )}

              {execution && (
                <ExecutionResult
                  session={session}
                  sql={version?.sql ?? execution.query.sql}
                  parameters={execution.query.parameters}
                  executionOverride={execution}
                  immutableSnapshot
                  compact
                  pageSize={10}
                />
              )}

              <div className="query-turn__footer">
                {validation && (
                  <span className="query-turn__footer-item">{validation}</span>
                )}
                {execution?.status === "succeeded" &&
                  execution.result !== undefined && (
                    <span className="query-turn__footer-item">
                      {rowLabel(execution.result.rowCount.returned)}
                    </span>
                  )}
                {execution && (
                  <span className="query-turn__footer-item">
                    {execution.durationMs} ms
                  </span>
                )}
                <button
                  type="button"
                  className="query-turn__footer-link"
                  onClick={() => onOpenDetails(turn.turnId)}
                >
                  details
                </button>
                {previousVersionOrdinal !== null && version && (
                  <button
                    type="button"
                    className="query-turn__footer-link"
                    onClick={() => onOpenDetails(turn.turnId, "versions")}
                  >
                    diff v{previousVersionOrdinal}→v{version.ordinal}
                  </button>
                )}
              </div>
            </section>
          )}
        </div>
      </article>
    );
  };

  return (
    <section className="turn-notebook" aria-label="Iterative query notebook">
      <ol className="turn-notebook__timeline">
        {turns.map((turn) => (
          <li key={turn.turnId}>{renderCell(turn)}</li>
        ))}
        {activeCell && (
          <li>
            <article className="query-turn query-turn--active">
              <div className="query-turn__gutter" aria-hidden="true">
                [{turns.length + 1}]
              </div>
              <div className="query-turn__body">{activeCell}</div>
            </article>
          </li>
        )}
        {!activeCell && (
          <li className="turn-notebook__composing" aria-hidden="true">
            <span className="query-turn__gutter">[{turns.length + 1}]</span>
            <span>composing…</span>
          </li>
        )}
      </ol>

      {/*
        A stable slot: rendering the pill as a bare sibling shifted every
        following child's position, so React remounted the composer each time
        it appeared or vanished — losing focus and flickering mid-scroll.
      */}
      <div className="turn-composer__jump-slot">
        {composerMode === "tucked" && (
        <button
          type="button"
          className="turn-composer__jump"
          onClick={() => {
            setScrollMode("full");
            window.scrollTo({
              top: document.documentElement.scrollHeight,
              behavior: "smooth",
            });
          }}
        >
          ↓ back to [{turns.length}] · ask
        </button>
        )}
      </div>

      <section
        id="refine-openelis"
        className="turn-composer"
        aria-labelledby="refine-query-title"
        data-mode={composerMode}
        data-failed={lastRunFailed ? "true" : undefined}
      >
        <div className="turn-composer__heading">
          <div className="turn-composer__title">
            <h2 id="refine-query-title">{composerTitle}</h2>
            {baseVersion ? (
              <p>
                Based on Query v{baseVersion.ordinal} — {versionAuthor(baseVersion)}
                {versionModel(baseVersion) ? ` — ${versionModel(baseVersion)}` : ""}
              </p>
            ) : (
              <p>Based on unresolved editor input</p>
            )}
          </div>
          {editorState === "unresolved" && (
            <Tag type="warm-gray">Unresolved editor input</Tag>
          )}
        </div>
        <button
          type="button"
          id="refine-openelis-toggle"
          className="turn-composer__restore"
          aria-expanded={composerMode === "full"}
          aria-controls="refine-openelis-body"
          onClick={restoreComposer}
        >
          <span>{composerTitle}</span>
          <span aria-hidden="true">⌘↵</span>
          <span aria-hidden="true">▴</span>
        </button>
        <form
          id="refine-openelis-body"
          className="turn-composer__form"
          hidden={composerMode !== "full"}
          onSubmit={handleSubmit}
        >
          <label className="visually-hidden" htmlFor="catalyst-followup">
            Follow-up instruction
          </label>
          <textarea
            id="catalyst-followup"
            ref={composerRef}
            rows={2}
            value={instruction}
            disabled={busy}
            onFocus={() => setComposerFocused(true)}
            onBlur={() => setComposerFocused(false)}
            onChange={(event) => onInstructionChange(event.currentTarget.value)}
            placeholder="Ask a question, or say how you want the current query changed"
          />
          <div className="turn-composer__toolbar">
            <label htmlFor="catalyst-followup-profile">
              <span>Model profile</span>
              <select
                id="catalyst-followup-profile"
                value={noRevisionProfiles ? "" : selectedProfileId}
                disabled={busy || noRevisionProfiles}
                onChange={(event) => onProfileChange(event.currentTarget.value)}
              >
                {noRevisionProfiles && (
                  <option value="">No revision-capable profile available</option>
                )}
                {revisionProfiles.map((profile) => (
                  <option
                    key={profile.id}
                    value={profile.id}
                    label={profileOptionLabel(profile)}
                    aria-label={profileOptionLabel(profile)}
                  >
                    {profile.label}
                  </option>
                ))}
              </select>
            </label>
            <span
              className="turn-composer__grounding"
              data-kind={grounding.kind}
              role="status"
            >
              {grounding.text}
            </span>
            <Button
              type="submit"
              disabled={editorEmpty || busy || noRevisionProfiles}
              aria-describedby={
                noRevisionProfiles
                  ? "catalyst-followup-profile-unavailable"
                  : undefined
              }
            >
              {generating ? "Generating next query…" : "Generate next query"}
            </Button>
          </div>
          {noRevisionProfiles && (
            <p id="catalyst-followup-profile-unavailable" role="status">
              No revision-capable model profile is currently available. Load a
              configured model profile to generate the next query.
            </p>
          )}
        </form>
      </section>

    </section>
  );
};
