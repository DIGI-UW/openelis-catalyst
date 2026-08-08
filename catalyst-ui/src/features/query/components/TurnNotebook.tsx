import { Button, Tag } from "@carbon/react";
import { useMemo, useState, type FormEvent } from "react";
import type {
  QueryProfile,
  WorkbenchExecution,
  WorkbenchGenerationEvidence,
  WorkbenchQueryVersion,
  WorkbenchSession,
} from "../types";
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
  evidence?: WorkbenchGenerationEvidence | null;
  evidenceLoadingTurnId?: string | null;
  evidenceError?: string | null;
  onInstructionChange: (instruction: string) => void;
  onProfileChange: (profileId: string) => void;
  onGenerate: () => void;
  onShowEvidence: (turnId: string) => void;
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

const EvidenceDetail = ({
  evidence,
}: {
  evidence: WorkbenchGenerationEvidence;
}) => {
  const profile = evidence.profile?.detail;
  const includedHistory = evidence.history?.included?.length ?? 0;
  const omittedHistory = evidence.history?.omitted?.length ?? 0;

  return (
    <section
      className="turn-evidence"
      aria-labelledby="turn-evidence-title"
    >
      <div className="turn-evidence__heading">
        <div>
          <h3 id="turn-evidence-title">
            Generation evidence for Query turn {evidence.turnId}
          </h3>
          <p>
            Recorded model calls and artifacts. This does not expose hidden
            reasoning.
          </p>
        </div>
        {evidence.status && <Tag type="purple">{evidence.status}</Tag>}
      </div>

      <dl className="turn-evidence__summary">
        <div>
          <dt>Profile</dt>
          <dd>{profile?.profileName ?? evidence.profile?.profileId ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Writer</dt>
          <dd>{profile?.writer?.modelId ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Reviewer</dt>
          <dd>{profile?.reviewer?.modelId ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Model time</dt>
          <dd>
            {evidence.totalInvocationDurationMs === null ||
            evidence.totalInvocationDurationMs === undefined
              ? "Unavailable"
              : `${evidence.totalInvocationDurationMs} ms`}
          </dd>
        </div>
        <div>
          <dt>Included history</dt>
          <dd>{includedHistory}</dd>
        </div>
        <div>
          <dt>Omitted history</dt>
          <dd>{omittedHistory}</dd>
        </div>
      </dl>

      <div className="turn-evidence__columns">
        <section>
          <h4>Model invocations</h4>
          {evidence.invocations.length === 0 ? (
            <p>No model invocation was recorded.</p>
          ) : (
            <ol>
              {evidence.invocations.map((invocation) => (
                <li key={invocation.invocationId}>
                  <strong>
                    {invocation.role} — {invocation.modelId}
                  </strong>
                  <span>
                    {invocation.stage}; attempt {invocation.attempt}; {invocation.outcome}
                    {invocation.durationMs === null
                      ? ""
                      : `; ${invocation.durationMs} ms`}
                  </span>
                  <code>{invocation.requestDigest}</code>
                </li>
              ))}
            </ol>
          )}
        </section>
        <section>
          <h4>Candidate disposition</h4>
          {!evidence.candidates || evidence.candidates.length === 0 ? (
            <p>No candidate artifact was recorded.</p>
          ) : (
            <ol>
              {evidence.candidates.map((candidate) => (
                <li key={candidate.candidateId}>
                  <strong>{candidate.role}</strong>
                  <span>{candidate.disposition.replaceAll("_", " ")}</span>
                  <span>
                    {candidate.versionRef
                      ? `Query ${candidate.versionRef.versionId}`
                      : "No immutable query version"}
                  </span>
                  {candidate.rawEvidence.inspectable &&
                    candidate.rawEvidence.exactPayload !== null && (
                      <details className="turn-evidence__payload">
                        <summary>
                          {candidate.role === "writer" ? "Writer" : "Reviewer"}{" "}
                          attempt {candidate.attemptOrdinal} raw evidence —{" "}
                          {candidate.disposition.replaceAll("_", " ")}
                        </summary>
                        <pre>
                          {JSON.stringify(
                            candidate.rawEvidence.exactPayload,
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    )}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      {[evidence.hubRequest, evidence.hubResponse].map((artifact, index) => {
        if (!artifact?.inspectable || artifact.exactPayload === null) return null;
        const label = index === 0 ? "Recorded Hub request" : "Recorded Hub response";
        return (
          <details key={label} className="turn-evidence__payload">
            <summary>{label}</summary>
            <pre>{JSON.stringify(artifact.exactPayload, null, 2)}</pre>
          </details>
        );
      })}
    </section>
  );
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
  evidence = null,
  evidenceLoadingTurnId = null,
  evidenceError = null,
  onInstructionChange,
  onProfileChange,
  onGenerate,
  onShowEvidence,
}: TurnNotebookProps) => {
  const [turnVisibilityOverrides, setTurnVisibilityOverrides] = useState<
    Record<string, boolean>
  >({});
  const [composerMinimized, setComposerMinimized] = useState(false);
  const revisionProfiles = useMemo(
    () => profiles.filter(
      (profile) => profile.available && profile.revisionCapable === true,
    ),
    [profiles],
  );
  const noRevisionProfiles = revisionProfiles.length === 0;
  const composerTitle = baseVersion
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
    const evidenceLoading = evidenceLoadingTurnId === turn.turnId;
    const outcome = cellOutcome(turn);
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
                <div className="query-turn__sql">
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
                <Button
                  type="button"
                  kind="ghost"
                  size="sm"
                  disabled={evidenceLoading}
                  onClick={() => onShowEvidence(turn.turnId)}
                >
                  {evidenceLoading
                    ? "Loading generation evidence…"
                    : "View generation evidence"}
                </Button>
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
        <li className="turn-notebook__composing" aria-hidden="true">
          <span className="query-turn__gutter">[{turns.length + 1}]</span>
          <span>composing…</span>
        </li>
      </ol>

      <section
        id="refine-openelis"
        className="turn-composer"
        aria-labelledby="refine-query-title"
        data-minimized={composerMinimized}
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
          <Button
            id="refine-openelis-toggle"
            type="button"
            kind="ghost"
            size="sm"
            aria-expanded={!composerMinimized}
            aria-controls="refine-openelis-body"
            onClick={() => setComposerMinimized((current) => !current)}
          >
            {composerMinimized ? "Expand" : "Minimize"}
          </Button>
        </div>
        <form
          id="refine-openelis-body"
          className="turn-composer__form"
          hidden={composerMinimized}
          onSubmit={handleSubmit}
        >
          <label className="visually-hidden" htmlFor="catalyst-followup">
            Follow-up instruction
          </label>
          <textarea
            id="catalyst-followup"
            rows={composerMinimized ? 1 : 2}
            value={instruction}
            disabled={busy}
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

      {evidenceError && (
        <p className="turn-evidence__error" role="alert">{evidenceError}</p>
      )}
      {evidence && <EvidenceDetail evidence={evidence} />}
    </section>
  );
};
