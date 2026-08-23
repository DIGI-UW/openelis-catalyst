import { DataBase, WarningAltFilled } from "@carbon/icons-react";
import { Button, Tag } from "@carbon/react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type {
  QueryProfile,
  WorkbenchExecution,
  WorkbenchQueryVersion,
  WorkbenchSession,
} from "../types";
import type { DetailsTab } from "./DetailsPanel";
import { lineDiffSummary } from "../lineDiff";
import { highlightSql } from "./sqlHighlight";
import { formatPostgresqlSql } from "./sqlEditorSupport";
import { ExecutionResult } from "./WorkbenchPanel";
import "./TurnNotebook.css";

/** The layout both diff sides share; unformattable text stays as written. */
const comparableSqlText = (sql: string): string => {
  try {
    return formatPostgresqlSql(sql);
  } catch {
    return sql;
  }
};

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
  failure: {
    message: string;
    /** The failure's own code; `needs_clarification` is a question, not a fault. */
    code?: string;
    /** The named checks that failed, straight from the failure diagnostic. */
    checks?: { name: string; value: string }[];
  } | null;
  /** Run recorded against this turn's selected version, if it has been run. */
  execution?: WorkbenchExecution | null;
  /** Advisory validation status for this turn's selected version. */
  validationStatus?: "invalid" | "warning" | "valid" | null;
  /** True for the turn whose selected version is the session's current one. */
  current?: boolean;
  /** When this happened. The clock both kinds of cell share. */
  createdAt: string;
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
  /** A failed workbench action, reported where the action was taken. */
  error?: string | null;
  onInstructionChange: (instruction: string) => void;
  onProfileChange: (profileId: string) => void;
  onGenerate: () => void;
  /** Open the Details panel scoped to this turn, on a chosen tab. */
  onOpenDetails: (turnId: string, tab?: DetailsTab) => void;
  /** Take the candidate a failed turn retained into the editor. */
  onEditAttempt?: (versionId: string) => void;
  /** Promote this turn's result into the Datasets library. */
  onSaveDataset?: () => void;
  /**
   * The editable current query, rendered as the last cell in the stack: the
   * work in progress sits where the next committed turn will, rather than in
   * a panel detached from the thread it belongs to.
   */
  activeCell?: ReactNode;
  /** The active draft differs from the current version, so its cell says so. */
  draftDivergent?: boolean;
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

/**
 * The turn ended on something only the person who asked can settle — a field
 * the data does not have. Nothing here malfunctioned, so the cell asks rather
 * than reporting a fault.
 */
const asksTheReader = (turn: NotebookTurn) =>
  turn.failure?.code === "needs_clarification";

/**
 * Who wrote this cell's query, as its own channel.
 *
 * Outcome and authorship are different questions and cannot share one
 * attribute: a hand-edited cell that succeeded is both, and a collapsed header
 * showed neither. §10 gives purple to the model, so the gutter carries it.
 */
const cellAuthor = (turn: NotebookTurn): "model" | "human" | "reviewer" => {
  const version = selectedVersionOf(turn);
  if (!version) return "model";
  const collaborationRole = textAt(version.provenance, "collaborationRole");
  if (version.authorType === "model_repair" || collaborationRole === "reviewer") {
    return "reviewer";
  }
  return version.authorType === "human" ? "human" : "model";
};

/** The right-hand summary in a collapsed header: `v3 · 12 rows`. */
/**
 * A completed turn whose profile declares a reviewer, but whose selected
 * version no reviewer ever signed off: no reviewer output, and no recorded
 * query_review check. Silence here read as approval, which is the one thing
 * an unreviewed query must not do.
 */
const isUnreviewed = (turn: NotebookTurn, version: NotebookVersion | null) => {
  if (turn.status !== "completed") return false;
  if (!turn.profileSnapshot.reviewer) return false;
  if (turn.outputVersions.some((output) => output.role === "reviewer")) {
    return false;
  }
  const provenance = (version?.provenance ?? {}) as Record<string, unknown>;
  if (typeof provenance.collaborationRole === "string") return false;
  const validation = provenance.generationValidation as
    | { checks?: { name?: unknown }[] }
    | undefined;
  const reviewed = (validation?.checks ?? []).some(
    (check) => String(check?.name ?? "").startsWith("query_review"),
  );
  return !reviewed;
};

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
  error = null,
  onInstructionChange,
  onProfileChange,
  onGenerate,
  onOpenDetails,
  onEditAttempt,
  onSaveDataset,
  activeCell = null,
  draftDivergent = false,
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
  // Each executed turn shows its own result. Minimising is per cell, because
  // "I have seen this one" is a judgement about that turn, not the thread.
  const [minimisedResults, setMinimisedResults] = useState<
    Record<string, boolean>
  >({});
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
      intent = 0;

      setScrollMode((current) => {
        // Position, not travel direction. catalyst#35 survived an
        // accumulated-intent gate because a real browser applies momentum: an
        // 8px wheel tick scrolls far enough to satisfy any travel threshold,
        // so a wobble at the end read as "up, then down, then up" and the
        // composer flickered full/line/full. Where you *are* cannot wobble
        // like that, and it is a measurement the composer's own height cannot
        // invert. Bands overlap, so leaving a mode needs more than entering
        // it, and `line` is the middle band rather than a one-pixel edge.
        if (gap < NEAR_END) return "full";
        if (gap > FAR_BACK) return "tucked";
        if (current === "full") return gap < LEAVE_END ? "full" : "line";
        if (current === "tucked") return gap > LEAVE_FAR ? "tucked" : "line";
        return "line";
      });
    };

    const onScroll = () => {
      if (frame === 0) frame = window.requestAnimationFrame(measure);
    };

    // A page that cannot scroll has no history to be scrolled back into, so
    // "tucked" and "line" describe a position that no longer exists — and no
    // scroll gesture can clear them, because a page with nothing to scroll
    // emits no scroll events. Content shrinking below the viewport is the way
    // in: the editor stepping aside when a run lands does it routinely, and it
    // became routine once the editor started presenting SQL laid out.
    const settleWhenUnscrollable = () => {
      if (document.documentElement.scrollHeight <= window.innerHeight) {
        lastY = 0;
        intent = 0;
        setScrollMode("full");
      }
    };

    settleWhenUnscrollable();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", settleWhenUnscrollable);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(settleWhenUnscrollable);
    observer?.observe(document.documentElement);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", settleWhenUnscrollable);
      observer?.disconnect();
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
  const latestOrdinal = turns.at(-1)?.ordinal;
  const composerTitle = lastRunFailed
    ? "Last run failed"
    : baseVersion
      ? `Refine ${latestOrdinal ? `[${latestOrdinal}]` : "the current query"}`
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

  // The tucked composer has always shown a ⌘↵ hint; this is what makes it true.
  // Ctrl is accepted alongside Command so the shortcut works on a keyboard that
  // has no Command key. Enter on its own still starts a new line: an instruction
  // is prose, and prose sometimes runs to a second line.
  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey)) return;
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
    const parentVersion = (() => {
      const parentId = version
        ? session.versions.find((item) => item.versionId === version.versionId)
            ?.parentVersionId
        : null;
      if (!parentId) return null;
      return session.versions.find((item) => item.versionId === parentId) ?? null;
    })();
    const previousVersionOrdinal = parentVersion?.ordinal ?? null;
    // The one-glance answer to "how big was the hand edit?" — the full
    // comparison stays behind "what changed". Both sides go through the same
    // formatter first: the parent may be stored as the model's one dense line
    // while the edit was made on the formatted text, and a diff that counts
    // the reflow would report "+7 −1" for a one-line change.
    const editDiff =
      version?.authorType === "human" && parentVersion
        ? lineDiffSummary(
            comparableSqlText(parentVersion.sql),
            comparableSqlText(version.sql),
          )
        : null;
    // The visible header truncates the instruction to one line, so the
    // accessible name carries it in full alongside the run counter it is cited
    // by and the outcome the status dot encodes visually.
    const unreviewed = isUnreviewed(turn, version);
    const headerLabel =
      `Query turn ${turn.ordinal}: ${turn.instruction} — ${outcome}` +
      (unreviewed ? " — unreviewed" : "");

    return (
      <article
        className="query-turn"
        id={`turn-${turn.ordinal}`}
        key={turn.turnId}
        data-status={status}
        data-author={cellAuthor(turn)}
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
            {/*
              Visible while the cell is collapsed, because that is exactly when
              an unreviewed query would otherwise pass unnoticed.
            */}
            {unreviewed && (
              <span className="query-turn__unreviewed">unreviewed</span>
            )}
            <span className="query-turn__outcome">{outcome}</span>
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
                    {versionAuthor(version)}
                    {versionModel(version) ? ` · ${versionModel(version)}` : ""}
                  </p>
                  {/*
                    Highlighted by parsing, not by mounting an editor: a long
                    thread would otherwise pay for one CodeMirror view per
                    cell to render text nobody can type into.
                  */}
                  <pre>
                    {highlightSql(version.sql).map((span, index) => (
                      <span
                        className={span.className}
                        key={`${index}-${span.text.length}`}
                      >
                        {span.text}
                      </span>
                    ))}
                  </pre>
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
                .map((output, index) => {
                  const retained =
                    turn.status === "failed" && output.role === "writer";
                  return (
                    <p
                      className="query-turn__superseded"
                      key={`${output.version!.versionId}-${index}`}
                    >
                      {retained
                        ? "Structured writer output — not selected"
                        : `${output.role} output — superseded`}
                      {/*
                        The attempt a failed turn kept is the shortest route
                        back to a working query, so the cell that reports the
                        failure is where it can be picked up.
                      */}
                      {retained && onEditAttempt && (
                        <button
                          type="button"
                          className="query-turn__footer-link"
                          onClick={() =>
                            onEditAttempt(output.version!.versionId)
                          }
                        >
                          Edit this attempt
                        </button>
                      )}
                    </p>
                  );
                })}

              {turn.status === "failed" && (
                <div
                  className={
                    asksTheReader(turn)
                      ? "query-turn__failure query-turn__failure--asking"
                      : "query-turn__failure"
                  }
                  role="status"
                >
                  <strong>
                    {asksTheReader(turn)
                      ? "Needs your answer"
                      : "Generation failed"}
                  </strong>
                  <p>
                    {turn.failure?.message ?? "The generation did not complete."}
                  </p>
                  {/*
                    Which checks failed, by name, so the reason is readable
                    here rather than only in Evidence.
                  */}
                  {turn.failure?.checks && turn.failure.checks.length > 0 && (
                    <dl className="query-turn__failure-checks">
                      {turn.failure.checks.map((check) => (
                        <div key={check.name}>
                          <dt>{check.name}</dt>
                          <dd>{check.value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              )}

              {/*
                A run is what the turn produced, so its result is presented as
                this turn's dataset rather than as a table repeated by a
                separate card. Saving promotes it to the Datasets library.
              */}
              {execution?.status === "succeeded" && version && (
                <div className="query-turn__dataset">
                  <div className="query-turn__dataset-heading">
                    <DataBase size={16} aria-hidden="true" />
                    <strong>Dataset from [{turn.ordinal}]</strong>
                    <Tag type="blue" size="sm">Draft</Tag>
                    <button
                      type="button"
                      className="query-turn__footer-link"
                      aria-expanded={!minimisedResults[turn.turnId]}
                      onClick={() =>
                        setMinimisedResults((current) => ({
                          ...current,
                          [turn.turnId]: !current[turn.turnId],
                        }))
                      }
                    >
                      {minimisedResults[turn.turnId] ? "Expand" : "Minimize"}
                    </button>
                    {turn.current && onSaveDataset && (
                      <Button
                        type="button"
                        kind="tertiary"
                        size="sm"
                        onClick={onSaveDataset}
                      >
                        Save to datasets
                      </Button>
                    )}
                  </div>
                  {!minimisedResults[turn.turnId] && (
                    <ExecutionResult
                      session={session}
                      sql={version.sql}
                      parameters={execution.query.parameters}
                      executionOverride={execution}
                      immutableSnapshot
                      compact
                      pageSize={10}
                    />
                  )}
                </div>
              )}
              {execution?.status === "failed" && (
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
                {editDiff && (editDiff.added > 0 || editDiff.removed > 0) && (
                  <span className="query-turn__footer-item">
                    +{editDiff.added} −{editDiff.removed} vs [
                    {previousVersionOrdinal}]
                  </span>
                )}
                {turn.current && execution && (
                  <span className="query-turn__footer-note">
                    {execution.status === "failed"
                      ? "The database diagnostic is available to the model; result row values are not."
                      : "Result row values are not included in model context."}
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
                    what changed
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
            <article
              className={`query-turn query-turn--active${
                draftDivergent ? " query-turn--provisional" : ""
              }`}
            >
              <div className="query-turn__gutter" aria-hidden="true">
                [{turns.length + 1}]
              </div>
              <div className="query-turn__body">
                {draftDivergent && (
                  <p className="query-turn__provisional" role="status">
                    Provisional draft — differs from{" "}
                    {baseVersion ? `[${baseVersion.ordinal}]` : "the current query"}.
                    Running it records a new version.
                  </p>
                )}
                {activeCell}
              </div>
            </article>
          </li>
        )}
        {!activeCell && !generating && (
          <li className="turn-notebook__composing" aria-hidden="true">
            <span className="query-turn__gutter">[{turns.length + 1}]</span>
            <span>composing…</span>
          </li>
        )}
        {/*
          Where the answer will appear, from the moment it is asked for. The
          composer's busy label is the only other signal and it can be
          scrolled out of sight, which reads as nothing happening at all.
        */}
        {generating && (
          <li>
            <article className="query-turn query-turn--pending">
              <div className="query-turn__gutter" aria-hidden="true">
                [{turns.length + (activeCell ? 2 : 1)}]
              </div>
              <div className="query-turn__body">
                <p className="query-turn__pending" role="status">
                  <span className="query-turn__pending-dot" aria-hidden="true" />
                  Generating the next query…
                </p>
              </div>
            </article>
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
                {versionAuthor(baseVersion)}
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
            onKeyDown={handleComposerKeyDown}
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
            {error && (
              <p className="turn-composer__error" role="alert">
                {error}
              </p>
            )}
            {grounding.kind === "stale" && (
              <span
                className="turn-composer__stale"
                role="img"
                aria-label={grounding.text}
                title={grounding.text}
              >
                <WarningAltFilled aria-hidden="true" />
              </span>
            )}
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
