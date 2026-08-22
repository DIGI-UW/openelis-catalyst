import { Tag } from "@carbon/react";
import { useEffect, useRef } from "react";
import type {
  WorkbenchGenerationEvidence,
  WorkbenchQueryVersion,
  WorkbenchSession,
  WorkbenchValidation,
} from "../types";
import "./DetailsPanel.css";

export type DetailsTab =
  | "validation"
  | "evidence"
  | "provenance"
  | "versions";

const TABS: Array<{ id: DetailsTab; label: string }> = [
  { id: "validation", label: "Validation" },
  { id: "evidence", label: "Evidence" },
  { id: "provenance", label: "Provenance" },
  { id: "versions", label: "Versions" },
];

interface DetailsPanelProps {
  session: WorkbenchSession;
  /** The turn this panel is scoped to; every tab repoints with it. */
  turnOrdinal: number | null;
  version: WorkbenchQueryVersion | null;
  validation: WorkbenchValidation | null;
  evidence: WorkbenchGenerationEvidence | null;
  evidenceLoading: boolean;
  evidenceError: string | null;
  tab: DetailsTab;
  developerMode: boolean;
  stacked: boolean;
  railWidth: number;
  onTabChange: (tab: DetailsTab) => void;
  onDeveloperModeChange: (developerMode: boolean) => void;
  onClose: () => void;
}

const shortDigest = (digest: string | undefined | null) =>
  digest ? `${digest.slice(0, 4)}…${digest.slice(-4)}` : "Unavailable";

const textAt = (source: Record<string, unknown> | undefined, key: string) => {
  const value = source?.[key];
  return typeof value === "string" && value ? value : undefined;
};

const recordAt = (source: Record<string, unknown> | undefined, key: string) => {
  const value = source?.[key];
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
};

const versionAuthor = (version: WorkbenchQueryVersion) => {
  const collaborationRole = textAt(version.provenance, "collaborationRole");
  if (version.authorType === "model_repair" || collaborationRole === "reviewer") {
    return "reviewer correction";
  }
  if (version.authorType === "model") return "model writer";
  return version.authorType.replaceAll("_", " ");
};

const ValidationTab = ({
  validation,
  developerMode,
}: {
  validation: WorkbenchValidation | null;
  developerMode: boolean;
}) => {
  if (!validation) {
    return (
      <p className="details-panel__empty">
        This query version has not been validated.
      </p>
    );
  }
  const statusTag =
    validation.status === "valid"
      ? { type: "green" as const, label: "Valid" }
      : validation.status === "warning"
        ? { type: "purple" as const, label: "Warnings" }
        : { type: "red" as const, label: "Findings" };

  return (
    <div className="details-panel__stack">
      <div className="details-panel__row">
        <Tag type={statusTag.type}>{statusTag.label}</Tag>
        <span className="details-panel__muted">
          validator {validation.validatorRevision} · {validation.durationMs} ms
        </span>
      </div>
      <p className="details-panel__note">
        Validation is advisory. Findings guide manual edits and never prevent a
        run.
      </p>
      {validation.findings.length === 0 ? (
        <p className="details-panel__empty">No findings were raised.</p>
      ) : (
        validation.findings.map((finding) => (
          <div className="details-panel__finding" key={finding.findingId}>
            <div className="details-panel__row">
              <strong>{finding.ruleCode}</strong>
              <Tag type="purple" size="sm">{finding.severity}</Tag>
            </div>
            <p>{finding.message}</p>
            <p className="details-panel__muted">
              <code>{finding.path}</code>
            </p>
            {finding.suggestedAction && (
              <p className="details-panel__muted">
                Suggested action: {finding.suggestedAction}
              </p>
            )}
          </div>
        ))
      )}
      {developerMode && (
        <p className="details-panel__mono">
          validated digest {shortDigest(validation.queryDigest)} · validator{" "}
          {shortDigest(validation.validatorDigest)}
        </p>
      )}
    </div>
  );
};

const EvidenceTab = ({
  evidence,
  loading,
  error,
  developerMode,
  onDeveloperModeChange,
}: {
  evidence: WorkbenchGenerationEvidence | null;
  loading: boolean;
  error: string | null;
  developerMode: boolean;
  onDeveloperModeChange: (developerMode: boolean) => void;
}) => (
  <div className="details-panel__stack">
    <p className="details-panel__note">
      Recorded model calls and artifacts. This does not expose hidden reasoning.
    </p>
    {error && (
      <p className="details-panel__empty" role="alert">
        {error}
      </p>
    )}
    {loading && <p className="details-panel__empty">Loading evidence…</p>}
    {!loading && !error && !evidence && (
      <p className="details-panel__empty">
        No generation evidence was recorded for this turn.
      </p>
    )}
    {evidence && (
      <>
        {evidence.invocations.length === 0 ? (
          <p className="details-panel__empty">
            No model invocation was recorded.
          </p>
        ) : (
          evidence.invocations.map((invocation) => (
            <div
              className="details-panel__invocation"
              key={invocation.invocationId}
            >
              <div className="details-panel__row">
                <strong>{invocation.role}</strong>
                <span className="details-panel__muted">
                  {invocation.modelId}
                </span>
                <span
                  className="details-panel__outcome"
                  data-outcome={invocation.outcome}
                >
                  {invocation.outcome}
                </span>
              </div>
              <p className="details-panel__muted">
                {invocation.stage} · attempt {invocation.attempt}
                {invocation.durationMs === null
                  ? ""
                  : ` · ${invocation.durationMs} ms`}
              </p>
              {developerMode && (
                <p className="details-panel__mono">
                  request {shortDigest(invocation.requestDigest)}
                </p>
              )}
            </div>
          ))
        )}
        {(evidence.candidates ?? []).map((candidate) =>
          candidate.rawEvidence.inspectable &&
          candidate.rawEvidence.exactPayload !== null ? (
            <details
              className="details-panel__disclosure"
              key={candidate.candidateId}
            >
              <summary>
                {candidate.role === "writer" ? "Writer" : "Reviewer"} candidate,
                attempt {candidate.attemptOrdinal} —{" "}
                {candidate.disposition.replaceAll("_", " ")}
              </summary>
              <pre>
                {JSON.stringify(candidate.rawEvidence.exactPayload, null, 2)}
              </pre>
            </details>
          ) : null,
        )}
        {[
          ["Recorded Hub request", evidence.hubRequest] as const,
          ["Recorded Hub response", evidence.hubResponse] as const,
        ].map(([label, artifact]) =>
          artifact?.inspectable && artifact.exactPayload !== null ? (
            <details className="details-panel__disclosure" key={label}>
              <summary>{label}</summary>
              <pre>{JSON.stringify(artifact.exactPayload, null, 2)}</pre>
            </details>
          ) : null,
        )}
      </>
    )}
    <label className="details-panel__developer">
      <input
        type="checkbox"
        checked={developerMode}
        onChange={(event) => onDeveloperModeChange(event.currentTarget.checked)}
      />
      Developer mode — show ids and digests inline everywhere
    </label>
  </div>
);

const ProvenanceTab = ({
  session,
  version,
}: {
  session: WorkbenchSession;
  version: WorkbenchQueryVersion | null;
}) => {
  const provenance = version?.provenance;
  const snapshot = recordAt(provenance, "profileSnapshot");
  const profile =
    textAt(snapshot, "profileLabel") ??
    textAt(snapshot, "profileName") ??
    textAt(snapshot, "profileId") ??
    textAt(provenance, "profileId") ??
    session.profileId;

  const entries: Array<[string, string]> = [
    ["Profile", profile],
    ["Session", session.sessionId.slice(0, 8)],
    ["Version", version ? `v${version.ordinal}` : "Unavailable"],
    ["Query digest", shortDigest(version?.queryDigest)],
    ["Catalog", session.catalogVersion],
    ["Dataset version", session.datasetVersion],
    ["Model", textAt(provenance, "model") ?? "Unavailable"],
    ["Author", version ? versionAuthor(version) : "Unavailable"],
  ];

  return (
    <dl className="details-panel__grid">
      {entries.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
};

const VersionsTab = ({
  session,
  version,
}: {
  session: WorkbenchSession;
  version: WorkbenchQueryVersion | null;
}) => (
  <ol className="details-panel__versions">
    {[...session.versions]
      .sort((left, right) => right.ordinal - left.ordinal)
      .map((candidate) => (
        <li
          key={candidate.versionId}
          data-current={
            candidate.versionId === session.currentVersionId ? "true" : undefined
          }
          data-scoped={
            candidate.versionId === version?.versionId ? "true" : undefined
          }
        >
          <div className="details-panel__row">
            <strong>Version {candidate.ordinal}</strong>
            {candidate.versionId === session.currentVersionId && (
              <Tag type="blue" size="sm">Current</Tag>
            )}
          </div>
          <p className="details-panel__muted">
            {versionAuthor(candidate)}
            {textAt(candidate.provenance, "model")
              ? ` · ${textAt(candidate.provenance, "model")}`
              : ""}
            {` · digest ${shortDigest(candidate.queryDigest)}`}
          </p>
        </li>
      ))}
  </ol>
);

export const DetailsPanel = ({
  session,
  turnOrdinal,
  version,
  validation,
  evidence,
  evidenceLoading,
  evidenceError,
  tab,
  developerMode,
  stacked,
  railWidth,
  onTabChange,
  onDeveloperModeChange,
  onClose,
}: DetailsPanelProps) => {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const scope =
    turnOrdinal === null
      ? "Session"
      : `Turn [${turnOrdinal}]${version ? ` · Query v${version.ordinal}` : ""}`;

  return (
    <>
      <button
        type="button"
        className="details-panel__scrim"
        aria-label="Close details"
        style={stacked ? undefined : { left: `${railWidth}px` }}
        onClick={onClose}
      />
      <aside
        className="details-panel"
        aria-label="Details"
        style={
          stacked
            ? undefined
            : { width: `min(28rem, calc(100vw - ${railWidth + 80}px))` }
        }
      >
        <div className="details-panel__header">
          <div>
            <p className="details-panel__eyebrow">DETAILS</p>
            <p className="details-panel__scope">{scope}</p>
          </div>
          <button
            type="button"
            ref={closeRef}
            className="details-panel__close"
            aria-label="Close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div role="tablist" aria-label="Details" className="details-panel__tabs">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`details-tab-${id}`}
              aria-selected={tab === id}
              aria-controls={`details-panel-${id}`}
              onClick={() => onTabChange(id)}
            >
              {label}
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          id={`details-panel-${tab}`}
          aria-labelledby={`details-tab-${tab}`}
          className="details-panel__body"
        >
          {tab === "validation" && (
            <ValidationTab
              validation={validation}
              developerMode={developerMode}
            />
          )}
          {tab === "evidence" && (
            <EvidenceTab
              evidence={evidence}
              loading={evidenceLoading}
              error={evidenceError}
              developerMode={developerMode}
              onDeveloperModeChange={onDeveloperModeChange}
            />
          )}
          {tab === "provenance" && (
            <ProvenanceTab session={session} version={version} />
          )}
          {tab === "versions" && (
            <VersionsTab session={session} version={version} />
          )}
        </div>
      </aside>
    </>
  );
};
