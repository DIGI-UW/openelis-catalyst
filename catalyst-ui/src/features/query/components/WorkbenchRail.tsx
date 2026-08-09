import {
  Asleep,
  Chat,
  ChartLine,
  Dashboard,
  DataBase,
  Light,
  Screen,
  Settings,
} from "@carbon/icons-react";
import {
  useEffect,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import type { ThemePreference } from "../theme";
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
  // "Ask" named the gesture; this names the place, which is what a nav is for
  // and what the section's own heading says once you are in it.
  { id: "ask", label: "Workbench", icon: Chat },
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
  sessionMenu: "closed" | "list" | "new" | "rename";
  onSessionMenuChange: (menu: "closed" | "list" | "new" | "rename") => void;
  onRenameSession: (name: string) => void;
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
  themePreference: ThemePreference;
  onThemePreferenceChange: (preference: ThemePreference) => void;
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
  onRenameSession,
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
  themePreference,
  onThemePreferenceChange,
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

      {/*
        The mark carries Carbon's AI treatment rather than a grey square: this
        product's whole claim is that a model wrote the query, and Carbon
        ships a designed vocabulary for exactly that. Both its tokens are
        theme-aware, so it holds up in dark without a second definition.
      */}
      <div className="workbench-rail__brand">
        <span className="workbench-rail__mark" aria-hidden="true">
          {/*
            A C that is also the thing the product does: an open arc taking
            something in, and a node where it comes out changed. A letterform
            alone said nothing, and at this size a literal glyph in a
            monospace face reads as a placeholder.
          */}
          <svg viewBox="0 0 32 32" role="presentation" focusable="false">
            <path
              d="M23 9.2a10 10 0 1 0 0 13.6"
              fill="none"
              stroke="currentColor"
              strokeWidth="3.4"
              strokeLinecap="round"
            />
            <circle cx="24.4" cy="16" r="3.1" fill="currentColor" />
          </svg>
        </span>
        <span className="workbench-rail__wordmark">
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
        {sessionMenu === "rename" && sessionName !== null ? (
          <form
            className="workbench-rail__session-rename"
            onSubmit={(event) => {
              event.preventDefault();
              onRenameSession(draftSessionName);
            }}
          >
            <label className="visually-hidden" htmlFor="catalyst-session-name">
              Session name
            </label>
            <input
              id="catalyst-session-name"
              autoFocus
              value={draftSessionName}
              placeholder="New session"
              onChange={(event) =>
                onDraftSessionNameChange(event.currentTarget.value)
              }
              onBlur={() => onRenameSession(draftSessionName)}
              onKeyDown={(event) => {
                if (event.key === "Escape") onSessionMenuChange("closed");
              }}
            />
          </form>
        ) : (
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
        )}

        {sessionMenu === "list" && (
          <div className="workbench-rail__session-menu" role="menu">
            {/* Pinned above a history that scrolls, so it is never buried. */}
            <button
              type="button"
              role="menuitem"
              className="workbench-rail__session-new"
              onClick={() => onSessionMenuChange("new")}
            >
              <span aria-hidden="true">＋ </span>New session…
            </button>
            {sessionName !== null && (
              <button
                type="button"
                role="menuitem"
                className="workbench-rail__session-rename-action"
                onClick={() => onSessionMenuChange("rename")}
              >
                Rename session
              </button>
            )}
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
          </div>
        )}

        {sessionMenu === "new" && (
          <div className="workbench-rail__session-menu workbench-rail__session-form">
            <p className="workbench-rail__session-menu-title">NEW SESSION</p>
            <label>
              <span>Name</span>
              <input
                value={draftSessionName}
                placeholder="Optional — defaults to your first question"
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
        <button
          type="button"
          className="workbench-rail__theme"
          aria-label={`Theme: ${themePreference}. Change it.`}
          title={`Theme: ${themePreference}`}
          onClick={() =>
            onThemePreferenceChange(
              themePreference === "system"
                ? "light"
                : themePreference === "light"
                  ? "dark"
                  : "system",
            )
          }
        >
          {themePreference === "dark" ? (
            <Asleep size={16} aria-hidden="true" />
          ) : themePreference === "light" ? (
            <Light size={16} aria-hidden="true" />
          ) : (
            <Screen size={16} aria-hidden="true" />
          )}
        </button>
      </div>

      <nav className="workbench-rail__nav" aria-label="Sections">
        {sections.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            title={label}
            aria-current={activeSection === id ? "page" : undefined}
            onClick={() => onSectionChange(id)}
          >
            <Icon size={18} aria-hidden="true" />
            {/*
              The label is the button's accessible name rather than an
              aria-label beside it, so what is read and what is seen are the
              same string. It hides itself only when the rail is too narrow to
              hold four of them.
            */}
            <span className="workbench-rail__nav-label">{label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
};
