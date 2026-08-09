import { Theme } from "@carbon/react";
import {
  DemoBanner,
  QueryWorkspace,
  type CatalystApi,
} from "./features/query";

interface AppProps {
  api?: CatalystApi;
  pollIntervalMs?: number;
}

export const App = ({ api, pollIntervalMs }: AppProps) => (
  /*
   * Gray 10, as §10 of the design contract mandates. Under White the page and
   * the cards are both #ffffff, so depth had to be faked with hairlines; under
   * Gray 10 the page is #f4f4f4 and layer-01 is #ffffff, and they separate for
   * free. It is also what makes CP-3's token mapping resolve to the values its
   * literals originally meant -- verified against g10 while the app still ran
   * White, which inverted every page-versus-card surface.
   */
  <Theme theme="g10">
    <div className="application">
      <DemoBanner />
      <QueryWorkspace api={api} pollIntervalMs={pollIntervalMs} />
    </div>
  </Theme>
);
