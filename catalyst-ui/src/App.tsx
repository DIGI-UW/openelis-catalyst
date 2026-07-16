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
  <Theme theme="white">
    <div className="application">
      <DemoBanner />
      <QueryWorkspace api={api} pollIntervalMs={pollIntervalMs} />
    </div>
  </Theme>
);
