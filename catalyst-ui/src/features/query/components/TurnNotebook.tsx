import { Button, Tag } from "@carbon/react";
import { useMemo, useState, type FormEvent } from "react";
import type {
  QueryProfile,
  WorkbenchGenerationEvidence,
  WorkbenchQueryVersion,
} from "../types";
import "./TurnNotebook.css";

type NotebookVersion = Pick<
  WorkbenchQueryVersion,
  "versionId" | "ordinal" | "authorType" | "queryDigest" | "provenance"
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
  dataSourceLabel?: string | null;
  status: "requested" | "completed" | "failed";
  selectedVersionId: string | null;
  outputVersions: NotebookOutputVersion[];
  profileSnapshot: {
    profileName: string | null;
    writer: { modelId: string } | null;
    reviewer: { modelId: string } | null;
  };
  failure: { message: string } | null;
}

export interface NotebookGrounding {
  kind: "matching" | "stale" | "not-executed";
  text: string;
}

interface TurnNotebookProps {
  turns: NotebookTurn[];
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

  const toggleTurn = (turnId: string) => {
    setTurnVisibilityOverrides((current) => ({
      ...current,
      [turnId]: !(current[turnId] ?? false),
    }));
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (editorEmpty || busy || noRevisionProfiles) return;
    onGenerate();
  };

  const renderTurn = (turn: NotebookTurn) => {
    const expanded = turnVisibilityOverrides[turn.turnId] ?? false;
    const regionId = `query-turn-${turn.turnId}`;
    return (
      <article className="query-turn" key={turn.turnId}>
        <button
          type="button"
          className="query-turn__disclosure"
          aria-expanded={expanded}
          aria-controls={regionId}
          onClick={() => toggleTurn(turn.turnId)}
        >
          <span className="query-turn__summary">
            <strong>Query turn {turn.ordinal}</strong>
            <span>{turn.instruction}</span>
          </span>
          {turn.dataSourceLabel && (
            <span className="query-turn__source">{turn.dataSourceLabel}</span>
          )}
          <span>{turn.status}</span>
        </button>
        {expanded && (
          <section
            id={regionId}
            className="query-turn__detail"
            aria-label={`Query turn ${turn.ordinal}`}
          >
            <p className="query-turn__instruction">{turn.instruction}</p>
            <p className="query-turn__profile">
              {turn.profileSnapshot.profileName ?? "Profile unavailable"}
            </p>
            {turn.outputVersions.map((output, index) => {
              const version = output.version;
              if (!version) return null;
              let summary: string;
              if (turn.status === "failed" && output.role === "writer") {
                summary = `Structured writer output — Query v${version.ordinal} — not selected`;
              } else if (output.selected) {
                summary = `Query v${version.ordinal} — selected ${output.role} output`;
              } else {
                summary = `Query v${version.ordinal} — ${output.role} output — superseded`;
              }
              return <p key={`${version.versionId}-${index}`}>{summary}</p>;
            })}
            {turn.status === "failed" && (
              <div className="query-turn__failure" role="status">
                <strong>Generation failed</strong>
                <p>{turn.failure?.message ?? "The generation did not complete."}</p>
              </div>
            )}
            <Button
              type="button"
              kind="ghost"
              size="sm"
              disabled={evidenceLoadingTurnId === turn.turnId}
              onClick={() => onShowEvidence(turn.turnId)}
            >
              {evidenceLoadingTurnId === turn.turnId
                ? "Loading generation evidence…"
                : "View generation evidence"}
            </Button>
          </section>
        )}
      </article>
    );
  };

  const earlierTurns = turns.slice(0, -1);
  const latestTurn = turns.at(-1);

  return (
    <section className="turn-notebook" aria-label="Iterative query notebook">
      <div className="turn-notebook__timeline">
        {earlierTurns.length > 0 && (
          <details className="turn-notebook__history">
            <summary>Earlier turns ({earlierTurns.length}) · read-only summaries</summary>
            <div>{earlierTurns.map(renderTurn)}</div>
          </details>
        )}
        {latestTurn && (
          <div className="query-turn__message">{latestTurn.instruction}</div>
        )}
        {latestTurn && renderTurn(latestTurn)}
      </div>

      <section
        id="refine-openelis"
        className="turn-composer"
        aria-labelledby="refine-query-title"
        data-minimized={composerMinimized}
      >
        <div className="turn-composer__heading">
          <div>
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
        <p
          className="turn-composer__grounding"
          data-kind={grounding.kind}
          role="status"
        >
          {grounding.text}
        </p>
        <form
          id="refine-openelis-body"
          className="turn-composer__form"
          hidden={composerMinimized}
          onSubmit={handleSubmit}
        >
          <label htmlFor="catalyst-followup">Follow-up instruction</label>
          <textarea
            id="catalyst-followup"
            rows={composerMinimized ? 1 : 3}
            value={instruction}
            disabled={busy}
            onChange={(event) => onInstructionChange(event.currentTarget.value)}
            placeholder="Describe how to revise the current query"
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
