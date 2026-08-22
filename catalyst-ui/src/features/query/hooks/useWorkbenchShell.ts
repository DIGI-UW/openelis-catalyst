import { useEffect, useState } from "react";
import type { DetailsTab } from "../components/DetailsPanel";
import {
  RAIL_DEFAULT_WIDTH,
  type RailSection,
} from "../components/workbenchRailSupport";
import type {
  DashboardBuilderSection,
  WorkbenchSessionSummary,
} from "../types";

/**
 * The workspace shell: which section is active, the rail's geometry, the
 * session menu, the details panel, and the viewport — everything about the
 * furniture, nothing about the data on it.
 *
 * One of five hooks extracted from QueryWorkspace. The setters are returned
 * as-is: workflows that cross clusters (adopting a session restores rail
 * layout, starting one returns to "ask") stay in the component, which is the
 * composer; this hook owns where the state lives.
 */
export const useWorkbenchShell = () => {
  const [activeSection, setActiveSection] =
    useState<DashboardBuilderSection>("ask");
  const [railWidth, setRailWidth] = useState(RAIL_DEFAULT_WIDTH);
  const [railSection, setRailSection] = useState<RailSection>("turns");
  const [activeTurnOrdinal, setActiveTurnOrdinal] = useState<number | null>(
    null,
  );
  const [sessionMenu, setSessionMenu] = useState<
    "closed" | "list" | "new" | "rename"
  >("closed");
  const [recentSessions, setRecentSessions] = useState<
    WorkbenchSessionSummary[]
  >([]);
  const [draftSessionName, setDraftSessionName] = useState("");
  const [detailsTurnId, setDetailsTurnId] = useState<string | null>(null);
  // Details can be scoped to the session rather than a turn: a gateway that
  // serves no per-turn evidence still records validation, provenance and
  // versions, and they must stay reachable.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsTab, setDetailsTab] = useState<DetailsTab>("validation");
  const [developerMode, setDeveloperMode] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? 1440 : window.innerWidth,
  );

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return {
    activeSection,
    setActiveSection,
    railWidth,
    setRailWidth,
    railSection,
    setRailSection,
    activeTurnOrdinal,
    setActiveTurnOrdinal,
    sessionMenu,
    setSessionMenu,
    recentSessions,
    setRecentSessions,
    draftSessionName,
    setDraftSessionName,
    detailsTurnId,
    setDetailsTurnId,
    detailsOpen,
    setDetailsOpen,
    detailsTab,
    setDetailsTab,
    developerMode,
    setDeveloperMode,
    viewportWidth,
  };
};
