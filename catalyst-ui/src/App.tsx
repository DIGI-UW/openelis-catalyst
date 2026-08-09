import { Theme } from "@carbon/react";
import { useThemePreference } from "./features/query/theme";
import {
  DemoBanner,
  QueryWorkspace,
  type CatalystApi,
} from "./features/query";

interface AppProps {
  api?: CatalystApi;
  pollIntervalMs?: number;
}

export const App = ({ api, pollIntervalMs }: AppProps) => {
  /*
   * Gray 10 for light, as §10 mandates, and Gray 100 for dark -- both are
   * already emitted in the bundle, so this is a theme swap rather than a
   * second stylesheet. Under White the page and the cards were both #ffffff
   * and depth had to be faked with hairlines; Gray 10 separates them for free.
   */
  const { preference, theme, choose } = useThemePreference();
  return (
    <Theme theme={theme}>
      <div className="application" data-theme={theme}>
        <DemoBanner />
        <QueryWorkspace
          api={api}
          pollIntervalMs={pollIntervalMs}
          themePreference={preference}
          onThemePreferenceChange={choose}
        />
      </div>
    </Theme>
  );
};
