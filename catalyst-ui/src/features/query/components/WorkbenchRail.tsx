import {
  Chat,
  ChartLine,
  Dashboard,
  DataBase,
  Settings,
} from "@carbon/icons-react";
import {
  useEffect,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import type {
  DashboardBuilderSection,
  DataSource,
  WorkbenchSessionSummary,
} from "../types";
import {
  clampRailWidth,
  RAIL_MIN_WIDTH,
  railMaxWidth,
  type RailSection,
  type RailTurn,
} from "./workbenchRailSupport";
import "./WorkbenchRail.css";

const sections: Array<{
  id: DashboardBuilderSection;
  label: string;
  icon: typeof Chat;
}> = [
  { id: "ask", label: "Ask", icon: Chat },
  { id: "datasets", label: "Datasets", icon: DataBase },
  { id: "widgets", label: "Widgets", icon: ChartLine },
  { id: "dashboards", label: "Dashboards", icon: Dashboard },
];

interface WorkbenchRailProps {
  width: number;
  stacked: boolean;
  onWidthChange: (width: number) => void;
  onWidthCommit: (width: number) => void;
  sessionName: string | null;
  sessionSourceLabel: string | null;
  sessionMenu: "closed" | "list" | "new";
  onSessionMenuChange: (menu: "closed" | "list" | "new") => void;
  recentSessions: WorkbenchSessionSummary[];
  onOpenSession: (sessionId: string) => void;
  activeSessionId: string | null;
  dataSources: DataSource[];
  draftSessionName: string;
  draftDataSourceId: string;
  onDraftSessionNameChange: (name: string) => void;
  onDraftDataSourceChange: (dataSourceId: string) => void;
  onStartSession: () => void;
  newSessionDisabled?: boolean;
  openSection: RailSection;
  onOpenSectionChange: (section: RailSection) => void;
  relationCount: number;
  turns: RailTurn[];
  activeTurnOrdinal: number | null;
  onSelectTurn: (ordinal: number) => void;
  onOpenDetails?: () => void;
  detailsOpen: boolean;
  activeSection: DashboardBuilderSection;
  onSectionChange: (section: DashboardBuilderSection) => void;
  children: ReactNode;
}

export const WorkbenchRail = ({
  width,
  stacked,
  onWidthChange,
  onWidthCommit,
  sessionName,
  sessionSourceLabel,
  sessionMenu,
  onSessionMenuChange,
  recentSessions,
  onOpenSession,
  activeSessionId,
  dataSources,
  draftSessionName,
  draftDataSourceId,
  onDraftSessionNameChange,
  onDraftDataSourceChange,
  onStartSession,
  newSessionDisabled = false,
  openSection,
  onOpenSectionChange,
  relationCount,
  turns,
  activeTurnOrdinal,
  onSelectTurn,
  onOpenDetails,
  detailsOpen,
  activeSection,
  onSectionChange,
  children,
}: WorkbenchRailProps) => {
  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (stacked) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    // The drag reports continuously so the notebook and composer track the
    // rail live, but only the width the pointer was released at is persisted.
    let current = startWidth;
    const move = (moveEvent: PointerEvent) => {
      current = clampRailWidth(
        startWidth + moveEvent.clientX - startX,
        window.innerWidth,
      );
      onWidthChange(current);
    };
    const stop = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", stop);
      document.body.style.userSelect = "";
      onWidthCommit(current);
    };
    document.body.style.userSelect = "none";
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", stop);
  };

  // A keyboard user resizes in steps rather than by dragging.
  const nudge = (delta: number) => {
    const next = clampRailWidth(width + delta, window.innerWidth);
    onWidthChange(next);
    onWidthCommit(next);
  };

  useEffect(() => {
    if (stacked) return;
    const onResize = () =>
      onWidthChange(clampRailWidth(width, window.innerWidth));
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, [onWidthChange, stacked, width]);

  const dataOpen = openSection === "data";
  const turnsOpen = openSection === "turns";

  return (
    <aside
      className="workbench-rail"
      aria-label="Catalyst"
      style={stacked ? undefined : { width: `${width}px` }}
      data-stacked={stacked ? "true" : undefined}
    >
      {!stacked && (
        <div
          className="workbench-rail__resize"
          role="separator"
          aria-label="Resize sidebar"
          aria-orientation="vertical"
          aria-valuenow={width}
          aria-valuemin={RAIL_MIN_WIDTH}
          aria-valuemax={railMaxWidth(
            typeof window === "undefined" ? 1440 : window.innerWidth,
          )}
          tabIndex={0}
          title="Drag to resize"
          onPointerDown={startResize}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              nudge(-32);
            } else if (event.key === "ArrowRight") {
              event.preventDefault();
              nudge(32);
            }
          }}
        />
      )}

      <div className="workbench-rail__brand">
        <span aria-hidden="true">C</span>
        <span>
          <strong>Catalyst</strong>
          <small>Governed queries → dashboards</small>
        </span>
      </div>

      <div className="workbench-rail__session">
        {/*
          The session owns its data source, so both live here rather than
          beside the model profile in the composer — a per-turn choice with a
          different lifetime. The source is always visible, never guessed at.
        */}
        <button
          type="button"
          className="workbench-rail__session-button"
          aria-label={`Session: ${sessionName ?? "none yet"}`}
          aria-expanded={sessionMenu !== "closed"}
          aria-haspopup="menu"
          onClick={() =>
            onSessionMenuChange(sessionMenu === "closed" ? "list" : "closed")
          }
        >
          <span>
            <strong>{sessionName ?? "No session yet"}</strong>
            <small>{sessionSourceLabel ?? "Choose a source to begin"}</small>
          </span>
          <span aria-hidden="true">▾</span>
        </button>

        {sessionMenu === "list" && (
          <div className="workbench-rail__session-menu" role="menu">
            <p className="workbench-rail__session-menu-title">
              RECENT SESSIONS
            </p>
            {recentSessions.length === 0 ? (
              <p className="workbench-rail__session-empty">
                No sessions recorded yet.
              </p>
            ) : (
              recentSessions.map((entry) => (
                <button
                  key={entry.sessionId}
                  type="button"
                  role="menuitem"
                  aria-current={
                    entry.sessionId === activeSessionId ? "true" : undefined
                  }
                  onClick={() => onOpenSession(entry.sessionId)}
                >
                  <span aria-hidden="true">
                    {entry.sessionId === activeSessionId ? "✓" : ""}
                  </span>
                  <span>
                    <span className="workbench-rail__session-entry-name">
                      {entry.name}
                    </span>
                    <span className="workbench-rail__session-entry-meta">
                      {[
                        dataSources.find(
                          (source) => source.id === entry.dataSourceId,
                        )?.label ?? entry.dataSourceId,
                        `${entry.turnCount} ${entry.turnCount === 1 ? "turn" : "turns"}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                </button>
              ))
            )}
            <button
              type="button"
              role="menuitem"
              className="workbench-rail__session-new"
              onClick={() => onSessionMenuChange("new")}
            >
              <span aria-hidden="true">＋ </span>New session…
            </button>
          </div>
        )}

        {sessionMenu === "new" && (
          <div className="workbench-rail__session-menu workbench-rail__session-form">
            <p className="workbench-rail__session-menu-title">NEW SESSION</p>
            <label>
              <span>Name</span>
              <input
                value={draftSessionName}
                placeholder="e.g. Turnaround time, Q3"
                onChange={(event) =>
                  onDraftSessionNameChange(event.currentTarget.value)
                }
              />
            </label>
            <label>
              <span>Data source</span>
              <select
                value={draftDataSourceId}
                onChange={(event) =>
                  onDraftDataSourceChange(event.currentTarget.value)
                }
              >
                {dataSources
                  .filter((source) => source.available)
                  .map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.label}
                    </option>
                  ))}
              </select>
            </label>
            <p className="workbench-rail__session-note">
              A session is grounded in one catalog. Its queries and versions
              can't move to another source later.
            </p>
            <div className="workbench-rail__session-actions">
              <button
                type="button"
                className="workbench-rail__session-start"
                disabled={newSessionDisabled}
                onClick={onStartSession}
              >
                Start session
              </button>
              <button
                type="button"
                onClick={() => onSessionMenuChange("closed")}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/*
        DATA and TURNS are mutually exclusive: whichever is open owns the
        rail's free height and scrolls internally, so neither can paint over
        the section nav below.
      */}
      <section
        className="workbench-rail__section"
        data-open={dataOpen ? "true" : undefined}
      >
        <h2>
          <button
            type="button"
            aria-expanded={dataOpen}
            onClick={() => onOpenSectionChange("data")}
          >
            <span aria-hidden="true">{dataOpen ? "▾" : "▸"}</span> DATA
            <span className="workbench-rail__count">
              {relationCount} {relationCount === 1 ? "relation" : "relations"}
            </span>
          </button>
        </h2>
        {dataOpen && <div className="workbench-rail__section-body">{children}</div>}
      </section>

      <section
        className="workbench-rail__section"
        data-open={turnsOpen ? "true" : undefined}
      >
        <h2>
          <button
            type="button"
            aria-expanded={turnsOpen}
            onClick={() => onOpenSectionChange("turns")}
          >
            <span aria-hidden="true">{turnsOpen ? "▾" : "▸"}</span> TURNS
            <span className="workbench-rail__count">{turns.length}</span>
          </button>
        </h2>
        {turnsOpen && (
          <div className="workbench-rail__section-body">
            <ol className="workbench-rail__turns">
              {turns.map((turn) => (
                <li key={turn.ordinal}>
                  <button
                    type="button"
                    data-status={turn.status}
                    aria-current={
                      activeTurnOrdinal === turn.ordinal ? "true" : undefined
                    }
                    onClick={() => onSelectTurn(turn.ordinal)}
                  >
                    <span className="workbench-rail__dot" aria-hidden="true" />
                    <span>
                      <span className="workbench-rail__turn-ordinal">
                        [{turn.ordinal}]
                      </span>{" "}
                      {turn.instruction}
                    </span>
                  </button>
                </li>
              ))}
              <li className="workbench-rail__composing" aria-hidden="true">
                <span className="workbench-rail__dot" />
                <span>[{turns.length + 1}] composing…</span>
              </li>
            </ol>
          </div>
        )}
      </section>

      <div className="workbench-rail__details">
        <button
          type="button"
          aria-expanded={detailsOpen}
          disabled={!onOpenDetails}
          onClick={onOpenDetails}
        >
          <Settings size={16} aria-hidden="true" />
          Details
        </button>
      </div>

      <nav className="workbench-rail__nav" aria-label="Sections">
        {sections.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            aria-label={label}
            title={label}
            aria-current={activeSection === id ? "page" : undefined}
            onClick={() => onSectionChange(id)}
          >
            <Icon size={18} aria-hidden="true" />
          </button>
        ))}
      </nav>
    </aside>
  );
};
