/**
 * The viewer's theme preference.
 *
 * Deliberately not in `browserState`, which the roadmap suggested: that is
 * per-session and server-persisted, and a theme is neither. It has to apply on
 * the landing screen before any session exists, and it belongs to the person
 * rather than to the thread they happen to be reading. localStorage, like the
 * active session id.
 *
 * Three states, not two. "system" is the default and follows the operating
 * system live; light and dark are explicit overrides that stop following it.
 */
import { useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";
/** Carbon's theme names. Gray 10 is what §10 mandates for light. */
export type CarbonTheme = "g10" | "g100";

const STORAGE_KEY = "catalyst.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

const readPreference = (): ThemePreference => {
  try {
    const stored = globalThis.localStorage?.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
};

const systemPrefersDark = () => {
  try {
    return globalThis.matchMedia?.(DARK_QUERY).matches ?? false;
  } catch {
    return false;
  }
};

export const resolveTheme = (
  preference: ThemePreference,
  prefersDark: boolean,
): CarbonTheme => {
  if (preference === "dark") return "g100";
  if (preference === "light") return "g10";
  return prefersDark ? "g100" : "g10";
};

export const useThemePreference = () => {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);
  const [prefersDark, setPrefersDark] = useState(systemPrefersDark);

  // Only while following the system: an explicit choice should not be
  // overridden by the operating system changing under it.
  useEffect(() => {
    if (preference !== "system") return;
    const media = globalThis.matchMedia?.(DARK_QUERY);
    if (!media?.addEventListener) return;
    const onChange = (event: MediaQueryListEvent) => setPrefersDark(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference]);

  const choose = (next: ThemePreference) => {
    setPreference(next);
    try {
      if (next === "system") globalThis.localStorage?.removeItem(STORAGE_KEY);
      else globalThis.localStorage?.setItem(STORAGE_KEY, next);
    } catch {
      // A blocked storage still themes this page; it just will not persist.
    }
  };

  return {
    preference,
    theme: resolveTheme(preference, prefersDark),
    choose,
  };
};
