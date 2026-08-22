export const RAIL_MIN_WIDTH = 200;
// 240 was tight once the rail's text came up to a readable size: session
// names truncated early and the section counts crowded their headers.
export const RAIL_DEFAULT_WIDTH = 288;
/** Below this the rail stops being a column and stacks above the notebook. */
export const RAIL_STACK_BREAKPOINT = 672;

export type RailSection = "data" | "turns";

export interface RailTurn {
  ordinal: number;
  instruction: string;
  status: "succeeded" | "failed" | "not-run";
  current: boolean;
}

/**
 * Reading the catalog at length is a resize, not a navigation: the rail can be
 * dragged out to half the window or more, then dragged back. That is why there
 * is no separate catalog page.
 */
export const railMaxWidth = (viewportWidth: number) =>
  Math.max(Math.round(viewportWidth * 0.5), viewportWidth - 280);

export const clampRailWidth = (width: number, viewportWidth: number) =>
  Math.min(railMaxWidth(viewportWidth), Math.max(RAIL_MIN_WIDTH, width));
