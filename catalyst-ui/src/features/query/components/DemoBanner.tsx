import { AskOpenElisNavigation } from "./AskOpenElisNavigation";

/**
 * The application's top strip.
 *
 * It used to lead with a warning icon and "Demo data only; not for clinical
 * decision-making." — a clinical-safety alarm on a screen whose every other
 * signal already says demo, and the first thing on every page.
 *
 * None of the eight numbered demo safeguards in docs/specification.md is a
 * banner: they are about synthetic data, local models, read-only database
 * identities and an explicit Run action. All of those are untouched. What is
 * gone is a label, not a control.
 *
 * The strip stays because it carries two things: the jump-to-composer control,
 * and the session meta positioned into its trailing edge.
 */
export const DemoBanner = () => (
  <header className="app-bar" aria-label="Catalyst">
    <div className="app-bar__inner">
      <span className="app-bar__spacer" />
      <AskOpenElisNavigation />
    </div>
  </header>
);
