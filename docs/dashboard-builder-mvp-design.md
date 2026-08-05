# Catalyst Dashboard Builder MVP design

**Status:** Authoritative UX input; Superset file/outbox MVP architecture approved
**Imported:** 2026-08-05 from the user-supplied `Dashboard builder MVP design.zip`  
**Interactive reference:** `docs/prototypes/dashboard-builder-mvp/`

This document preserves the complete supplied design handoff in the Catalyst
repository and is the UX/product-design source for the Dashboard Builder
workstream. The approved implementation boundary below supersedes conflicting
integration details in the original handoff while preserving its information
architecture, interaction design, and visual specification.

## Approved MVP reconciliation

The prototype's Ask shell, fixed composer, chronological thread, Dataset tile,
and review panel are the required target experience. Production must integrate
the accepted query notebook into them and retain profile/model selection and
evidence, exactly one SQL editor with completion/formatting/wrapping, manual
versions and unresolved snapshots, advisory Validate, explicit Run, visible raw generation evidence,
findings, database diagnostics and typed results, contextual follow-up, compact
history, result staleness, refresh restoration, runtime schema/catalog access,
and New session through **Save Dataset**. The mock's abbreviated prompt-only
state is not a license to omit those capabilities. Do not implement its example
prompts or implied automatic generation/execution. Reorganize the schema/data
context and executed-result preview into the compact thread, Dataset draft tile,
and review panel while keeping all existing information and actions accessible.

1. Superset 6.1.0 is the dashboard renderer. Catalyst does not build a parallel
   chart runtime. Because the MVP is a one-way file handoff, Catalyst remains the
   desired-configuration source of truth and Superset-only layout edits are
   overwritten on the next publication; collaborative Superset ownership is
   deferred with API/export reconciliation.
2. Catalyst owns persistent, immutable-versioned Dataset, Widget, and Dashboard
   drafts and their libraries. A dashboard may compose multiple saved widgets.
3. The initial visualization suggestion is deterministic from the typed result
   shape and is always reviewable/overridable. Model-generated visualization
   specifications are deferred.
4. `Publish to Superset` atomically creates a deterministic native Superset ZIP
   in a host-visible outbox mounted read-only into Superset and offers the same
   file for download. Stack bootstrap imports the selected bundle; an explicit
   CLI helper imports or updates it in an already-running instance.
   The logical Dashboard UUID remains stable; Dataset and Widget/chart UUIDs
   derive from immutable versions so Superset 6.1.0 can create changed children
   while overwriting the Dashboard that points to them.
5. The MVP does not call the Superset REST API. Catalyst shows `Bundle ready`
   until the importer records CLI success, then `Imported`; it never infers
   `Synced` merely because a file exists.
6. Superset queries the analytics database through the local demo read-only
   role. Bundles contain configuration/provenance rather than result rows. Only
   the labelled local demo may embed its read-only credential.
7. Superset API publication, embedded viewing, cross-system undo/reconciliation,
   sharing, scheduling, cache policy, production naming/authorization, and
   production secrets remain later decisions.

The prototype remains viewable with `scripts/serve-dashboard-prototype.sh`.
Where the original handoff below says API write, sync, push, or undo, implement
the approved outbox/import semantics above for this milestone.

# Original handoff: Catalyst Dashboard Builder MVP

## Overview

Catalyst today turns a natural-language question into governed SQL, runs it read-only, and returns a typed table. This design generalizes that into a full path:

**ask → dataset → widget → dashboard**

Catalyst is the *builder* and desired-configuration source for this one-way MVP.
**Superset is the renderer**: Catalyst publishes datasets, charts, and dashboard
configuration as a native ZIP to the shared outbox, and the pinned CLI imports
it. Filtering and viewing happen in Superset; Catalyst never re-implements the
chart runtime. API-backed collaboration and Superset-to-Catalyst reconciliation
remain post-MVP.

Repo this extends: `DIGI-UW/openelis-catalyst` (branch `main`), app at `catalyst-ui/`.

## About the Design Files

The files in this bundle are **design references created in HTML** — prototypes showing intended look and builder behavior, not production code to copy directly and not a replacement for the accepted Ask/query-notebook behavior. The task is to **recreate these designs in `catalyst-ui`** using its existing environment: React + TypeScript with IBM Carbon Design System conventions (the existing app already uses Carbon tokens, IBM Plex Sans/Mono, and Carbon component patterns in `catalyst-ui/src/features/query/`). Prefer real `@carbon/react` components (`DataTable`, `Tile`, `SideNav`, `Accordion`, `Button`, `Select`, `TextArea`, `Tag`, `InlineNotification`) over hand-rolled markup wherever a Carbon component matches what the prototype draws.

## Fidelity

**High-fidelity.** `Catalyst Dashboard Builder 4c.dc.html` carries final colors, typography, spacing, states, and interactions — recreate it faithfully, but express it through Carbon components and tokens rather than copying inline styles. `Dashboard Builder Wireframes.dc.html` is **low-fidelity** and is included only as design rationale (four structural directions considered, and why 4c won).

`Catalyst Query Screen.dc.html` is a recreation of the **existing** app screen,
included so its full query-workbench behavior can be integrated into the new Ask
screen and visually regressed against today's baseline.

## Product model

Four object types, mapped onto Superset primitives:

| Catalyst object | Lives in | Superset counterpart | Notes |
| --- | --- | --- | --- |
| Session / thread | Catalyst | — | question, generated SQL, drafts, provenance trace |
| **Dataset** | Catalyst + Superset | imported virtual-dataset YAML over governed SQL | one immutable Dataset version can back many Widget versions |
| **Widget** | Catalyst + Superset | imported chart YAML | stores compatible viz type + deterministic column bindings |
| **Dashboard** | Catalyst draft + Superset runtime | imported dashboard YAML | Catalyst publishes desired layout; Superset-only edits are replaced on republish in this MVP |

**Ordering constraint that drives the UI:** Superset cannot import a chart
without its dataset asset. Dataset save is strictly upstream of Widget save, and
the published ZIP orders database → dataset → chart → dashboard. Saving a Widget
may first save its Dataset version as one Catalyst operation; no Superset write
occurs until publication.

### MVP visualization set

Deliberately small, chosen for laboratory/health surveillance data:

1. **Table** — typed, as today. Default when the shape is not obviously chartable.
2. **Big number (KPI)** with optional trend vs. previous period.
3. **Time-series line/area** — the default whenever a date/time column plus a measure is present.
4. **Bar** — categorical comparison, grouped or stacked.
5. **Proportion bar** — 100% stacked single bar for composition (e.g. rejection reasons).

Pie/donut is intentionally excluded: proportions read more accurately as a stacked bar, and the demo data is mostly time series and categorical counts. Map and pivot table are explicit post-MVP.

### Chart suggestion rule

The MVP deterministically suggests one viz type from the typed result shape. The
user can override the compatible type with one compact control in the Widget
review panel; arbitrary column remapping and model-generated visualization
specifications remain deferred. Suggested precedence:

- 1 date/timestamp column + ≥1 numeric measure → **line** (split by a low-cardinality category if one exists, ≤ 6 distinct values)
- 0 date columns + 1 category + 1 measure → **bar**
- single row, single measure → **KPI**
- 1 category + 1 measure where measure sums to a meaningful whole → **proportion bar**
- anything else, or > 6 columns → **table**

The suggestion is presented as prose in the assistant bubble ("One date column,
one count, one category. I'd show this as a **monthly trend line, split by test
type**."). The panel shows the derived bindings read-only and one compatible-type
selector. There is no arbitrary column-mapping panel in the MVP; changing query
shape remains a normal query follow-up.

### Query parameters → dashboard filters

Generated SQL may contain named parameters (`:since_date`, `:facility`). The dataset panel lists them read-only in the MVP. Post-MVP: promote a parameter to a Superset native filter at placement time. Do not build the filter-mapping table in the MVP.

## Screens / Views

The app is a left-nav shell with four sections. Shell chrome is identical across all four.

### Shell

**Demo banner** (fixed, top, full width, `z-index: 120`)
- Height `2.5rem` min, background `#262626`, text `#f4f4f4`, bottom border `1px solid #8d8d8d`, padding `0.5rem 1.5rem`, gap `0.75rem`, font-size `0.875rem`.
- Carbon warning-circle icon 20×20, then a pill: height `1.125rem`, radius `0.5625rem`, background `#e5e0df`, color `#171414`, font-size `0.75rem`, text "Demo environment".
- Body copy: "Demo data only; not for clinical decision-making."
- Right-aligned session meta, color `#c6c6c6`, font-size `0.75rem`: "Session 7f2a91c4 · 3 turns" (or "New session" when the thread is empty).
- This is the existing `DemoBanner` component, restyled to a full-width fixed bar.

**Left nav** (fixed, `top: 2.5rem`, `bottom: 0`, `z-index: 100`)
- Expanded width `16rem`; collapsed width `3rem`; `transition: width 140ms`. Background `#fff`, right border `1px solid #e0e0e0`, padding `0.5rem 0 1rem`.
- Header (expanded only): "Catalyst" `0.875rem`/600 `#161616`; subtitle "Governed queries → dashboards" `0.75rem` `#6f6f6f`, `white-space: nowrap`.
- Collapse toggle: 2.5rem square icon button, chevron-left 20×20 `#525252`, rotates 180° when collapsed (`transition: transform 140ms`), hover background `#e8e8e8`, `aria-label="Toggle navigation"`, `aria-expanded`.
- Nav items: Ask · Datasets · Widgets · Dashboards. Each is a full-width button, `min-height: 2.5rem`, 16×16 Carbon icon, label `0.875rem`, right-aligned count `0.75rem` `#6f6f6f` with `font-variant-numeric: tabular-nums`. Collapsed: icon only, centered, `title` attribute carries the label.
- Active item: background `#e8e8e8`, `border-left: 3px solid #0f62fe`, color `#161616`, weight 600, `aria-current="page"`. Inactive: transparent background, transparent left border, `#525252`, weight 400. Hover: `#e8e8e8`.
- Counts are live: Datasets and Widgets increment as objects are saved.
- Footer (expanded only), above a `1px solid #e0e0e0` top border: label "Data source" `0.75rem` `#6f6f6f` and a Carbon Select — "OpenELIS laboratory (demo)" / "OpenMRS HIV/ART (demo)".

**Content column**: `margin-left` tracks nav width (`transition: margin-left 140ms`); inner container `width: min(100% - 3rem, 60rem)`, centered. Padding top `2rem`; bottom `14rem` on Ask (clears the composer) and `4rem` elsewhere.

**Page header pattern** (all four screens): eyebrow `0.75rem`/600 `#0f62fe`, `letter-spacing: 0.08em`, uppercase; H1 `2rem`/400, `letter-spacing: -0.025em`, `line-height: 1.15`; description `0.875rem` `#525252`, `line-height: 1.5`. Primary action, when present, sits top-right.

### 1. Ask — populated thread

Purpose: ask a question, review what came back, save it.

Behavioral override: retain the accepted production query workflow and controls
through Dataset save. The thread wraps that workflow; it does not collapse SQL
generation, editing, versioning, validation, explicit execution, diagnostics,
results, and follow-up into a single automatic prompt action. The executed-result
preview opens from the Dataset tile in the review panel, while the one canonical
SQL editor remains the active query work surface.

Thread is a single `flex-direction: column; gap: 1rem` stack, full content width.

- **Header**: eyebrow "Ask OpenELIS", H1 = the session title ("Monthly viral load, 2026"), description "Nothing is saved until you review it. Drafts stay in this thread." Top-right: "New session" ghost button (height `2rem`, `1px solid #0f62fe`, `#0f62fe`, add-16 icon).
- **User message**: `align-self: flex-end`, `max-width: 38rem`, padding `0.75rem 1rem`, background `#e0e0e0`, color `#161616`, `0.875rem`/1.5. No radius (Carbon is square).
- **Latest query workbench card**: immediately after the latest user instruction
  and before any Dataset tile, integrate the current production workbench. It is
  the only editable SQL surface and retains the current CodeMirror completion,
  formatting and wrapping controls; typed parameter editor; advisory validation;
  explicit Run; raw generation evidence; findings; database diagnostics; typed
  result status; provenance/version history; Clear/Restore; and New session. Use
  the thread's full content width and Carbon tile styling. After a successful Run,
  the one Dataset tile immediately following this card is the entry point to the
  typed row preview; do not retain a second inline result table. Earlier turns
  collapse to read-only question/query/version/execution summaries. When a
  successor becomes current, this same card moves with the latest turn rather
  than creating another editor.
- **Draft tile — dataset** (the key component). A button, `width: 100%`, `max-width: 34rem`, `display: flex; align-items: center; gap: 1rem`, padding `0.75rem 1rem`, background `#fff`, border `1px solid #c6c6c6`, `border-left: 3px solid` state accent. Contents left → right:
  - 20×20 Carbon "data-table" icon, `#525252`
  - stacked text (`flex: 1`): name `0.875rem`/600; meta line `0.75rem` `#6f6f6f` — "Dataset · 1,486 rows · 4 columns"
  - status pill: height `1.5rem`, radius `0.75rem`, `0.75rem` text. Draft = background `#fcf4d6` / color `#684e00`. Saved = background `#defbe6` / color `#0e6027`.
  - "Review" affordance, `#0f62fe`, `0.875rem`
  - Hover: `border-color: #0f62fe`, `background: #f4f4f4`. Left accent: `#0f62fe` while draft, `#24a148` once saved.
  - Whole tile is the click target; it opens the review panel. No data table and no chart render inline — detail lives in the panel only.
- **Assistant suggestion**: `max-width: 44rem`, padding `1rem 1.25rem`, `border-left: 3px solid #8a3ffc`, background `#f7f2ff`, `0.875rem`/1.5. Viz name in `<strong>`.
- **Draft tile — widget**: same geometry as the dataset tile; thumbnail is a 52×24 two-series sparkline (`#0f62fe` and `#a56eff`, `stroke-width: 2`). Meta line: "Line chart · split by test_name", becoming "Line chart · on Lab operations" after placement. Left accent `#8a3ffc` while draft, `#24a148` once saved.

**Composer** (fixed, bottom, `left` tracks nav width, `z-index: 90`)
- Padding `1rem 1.5rem calc(1rem + env(safe-area-inset-bottom))`, `border-top: 4px solid #0f62fe`, background `#fff`, `box-shadow: 0 -0.25rem 1rem rgb(0 0 0 / 18%)`. Inner container matches the content column.
- Field wrapper: `1px solid #8d8d8d`, background `#f4f4f4`; textarea area is `#fff`, padding `0.75rem 1rem`, `min-height: 3.5rem`, `font-size: 1rem`/1.5, `resize: none`, no visible border. Placeholder: "Ask a question, or say how you want the last result to look".
- Footer row, `border-top: 1px solid #c6c6c6`, padding `0.625rem 1rem`: model/exec note `0.75rem` `#6f6f6f` ("Gemma 4 12B writer · read-only execution") and a primary Send button (height `2.5rem`, background `#0f62fe`, hover `#0050e6`, arrow-right-16 icon, `gap: 2rem` between label and icon — the Carbon expressive button pattern already used in the app).
- Composer is present on Ask only.

### 2. Ask — first run / empty thread

Same shell and composer, no thread.
- H1 "What do you want to know?"; description "Ask in plain language. Catalyst
  prepares editable SQL; you review, validate, and explicitly run it read-only."
- Omit the prototype's three example-prompt buttons. They would bias manual
  evaluation and are not part of the accepted Ask experience.
- Footnote `0.75rem` `#6f6f6f`: "Queries run read-only against the governed catalog only when you select Run. You review the SQL before anything is saved."
- No "New session" button in this state.

### 3. Datasets library

Purpose: find and reuse a saved governed query.

- Header: eyebrow "Library", H1 "Datasets", description "Saved governed queries, ready for dashboard publication. One dataset can back many widgets." Primary button top-right: "New from question" (add-16 icon) → navigates to Ask.
- Carbon `DataTable` on a `#fff` surface with `box-shadow: 0 0.125rem 0.5rem rgb(0 0 0 / 8%)`. Header row background `#e8e8e8`, cells padding `0.75rem 1rem`, `0.875rem`, row separators `1px solid #e0e0e0`, zebra `#fff` / `#f4f4f4`.
- Columns: Name (weight 500) · Source · Columns · Widgets · Parameters (IBM Plex Mono `0.75rem` `#525252`) · Last run (`#525252`) · Status pill · row action "Review" (ghost button, `#0f62fe`, right-aligned).
- **Widgets count is the governance affordance** — it shows downstream use so nothing is deleted blind.
- Seed rows: "Turnaround time by test type" (OpenELIS · 6 · 2 · `:since_date` · Jul 12, 09:14 · Saved); "Rejected specimens by reason" (OpenELIS · 4 · 1 · — · Jul 10, 16:02 · Saved); "CD4 cohort, under 200" (OpenMRS · 5 · 0 · `:facility` · Jul 8, 11:47 · Draft). A dataset saved during the session appears at the top.
- Row "Review" opens the same dataset panel used in the thread.

### 4. Widgets library

Purpose: reuse a chart config on another dashboard.

- Header: eyebrow "Library", H1 "Widgets", description "Saved chart configurations. A widget can sit on more than one dashboard."
- `display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr))`.
- Card: background `#fff`, `box-shadow: 0 0.125rem 0.5rem rgb(0 0 0 / 8%)`, no radius.
  - Thumbnail band: height `7rem`, padding `1rem`, background `#f4f4f4`, `border-bottom: 1px solid #e0e0e0`, contents centered. One thumbnail per viz type: line = two polylines (`#0f62fe`, `#a56eff`); KPI = value `2rem`/600 plus trend line `0.75rem` `#0e6027`; bar = five `#0f62fe` rects; table = four stacked bars (`#8d8d8d` header, `#c6c6c6` rows); proportion = one 2.5rem-tall row split `#0f62fe` / `#78a9ff` / `#c6c6c6`.
  - Body: padding `1rem`, `gap: 0.375rem` — name `0.875rem`/600; "<type> · <dataset>" `0.75rem` `#6f6f6f`; placement `0.75rem` `#525252` ("On Lab operations", "On Lab operations · HIV/ART program", or "Not placed"); then an "Add to dashboard" ghost button (height `2rem`, `1px solid #0f62fe`).
- Seed cards: "Median turnaround, 30 days" (Big number, `212m`, "↓ 8% vs previous 30 days"); "Results by test type" (Bar); "Rejection reasons" (Proportion bar); "Pending results, current" (Table). A widget saved during the session appears first.

### 5. Dashboards

Purpose: see what exists, which bundle is ready/imported, and jump to Superset.

- Header: eyebrow "Library", H1 "Dashboards", description "Catalyst publishes the desired configuration; Superset imports and renders it."
- Rows stacked `gap: 1rem`. Each row: `display: flex; gap: 1.5rem`, padding `1.25rem 1.5rem`, background `#fff`, `box-shadow: 0 0.125rem 0.5rem rgb(0 0 0 / 8%)`, `border-left: 3px solid` — `#f1c21b` when a bundle is pending import, `#24a148` when the exact digest is Imported.
  - **Layout mirror** (left, `width: 9rem`): `display: grid; grid-template-columns: repeat(3, 1fr); grid-auto-rows: 1.5rem; gap: 0.25rem`; tiles span 1–3 columns. A newly added widget is `#0f62fe`; existing widgets `#c6c6c6`; empty space `#e0e0e0`. Read-only — it is a wayfinding hint, not an editor.
  - Middle: name `1rem`/600; meta `0.875rem` `#525252` ("6 widgets · 3 datasets"); publication line `0.75rem` — `#8e6a00` "Bundle ready — import pending", `#0e6027` "Imported · Jul 15, 14:02", or the explicit failed-import state.
  - Right: "Publish to Superset" primary button above "Download bundle" and an "Open Superset" ghost link with launch-16 icon, all height `2.5rem`.
- Seed rows: "Lab operations" (Bundle ready) and "HIV/ART program" (Imported Jul 12, 09:20).

### 6. Review panel (slide-over)

One panel component, two modes. Opened by any draft tile or library row; this is **the only place saves happen**.

- Scrim: fixed, `top: 2.5rem`, `left` = nav width, `right: 0`, `bottom: 0`, background `rgb(22 22 22 / 40%)`, `z-index: 130`; click closes.
- Panel: fixed right, `top: 2.5rem`, `bottom: 0`, `width: min(32rem, calc(100vw - 4rem))`, background `#fff`, `border-left: 1px solid #e0e0e0`, `box-shadow: -0.25rem 0 1rem rgb(0 0 0 / 18%)`, `z-index: 140`, `display: flex; flex-direction: column`.
- Underlying page gets `filter: blur(1.5px)` (`transition: filter 120ms`) — cheap depth cue; drop it if it costs paint performance.
- **Header**: padding `1.25rem 1.5rem`, `border-bottom: 1px solid #e0e0e0`. Kicker `0.75rem` `#6f6f6f` uppercase `letter-spacing: 0.08em` ("Dataset" / "Widget"); title `1.25rem`/400 `letter-spacing: -0.025em` ("Review dataset" / "Review widget"); 2.5rem close icon button (close-20), hover `#e8e8e8`.
- **Body**: `flex: 1; overflow-y: auto`, padding `1.5rem`, sections `gap: 1.5rem`.
- **Footer**: padding `1rem 1.5rem`, `border-top: 1px solid #e0e0e0`, background `#f4f4f4`; primary save + "Close" ghost, `gap: 0.75rem`, both height `2.5rem`.

**Dataset mode body**
1. Name text input (label `0.75rem` `#525252`; Carbon underline field: `background: #f4f4f4`, `border-bottom: 1px solid #8d8d8d`, `min-height: 2.5rem`).
2. Metadata grid, 2 columns, `gap: 1px` on a `#e0e0e0` background so hairlines show; each cell padding `0.75rem 1rem`, background `#f4f4f4`; `dt` `0.75rem` `#6f6f6f`, `dd` `1rem`. Rows: 1,486 · 4 · OpenELIS · `:since_date`.
3. Three-row result preview table, `0.75rem`, `1px solid #e0e0e0`, same header/zebra treatment as the libraries.
4. Collapsed accordion "SQL and provenance" (Carbon accordion; chevron rotates 90°, `transition: transform 110ms`). Expanded: `<pre>` IBM Plex Mono `0.75rem`/1.6 on `#f4f4f4`, padding `1rem`; then provenance line `0.75rem` `#6f6f6f`: "Profile catalyst-query-gemma-4-12b-q4 · lint passed · trace cat-7f2a91c4 · catalog analytics-catalog-v1".
   - **Governance decision:** this is a read-only snapshot of the exact executed
     Query vN and provenance. Editing remains in the latest turn's single
     canonical SQL editor. Saving the Dataset is explicit; later query changes
     mark the draft stale rather than mutating or silently rebinding it.
5. Footer: "Save dataset" → "Saved to library" (disabled) once saved.

**Widget mode body**
1. Schematic preview, padding `1rem`, `1px solid #e0e0e0`: preserve the mock's
   420×150 geometry as a lightweight type thumbnail, not a Catalyst chart
   renderer. Authoritative data rendering happens in Superset after import.
2. Widget name input plus one compact compatible-visualization selector. Derived
   bindings and incompatibility reasons are read-only.
3. "Reads" block: label `0.75rem` `#525252`, then a `#f4f4f4` row (padding `0.75rem 1rem`) with dataset name and its Draft/Saved pill. When the dataset is unsaved, a `0.75rem` `#8e6a00` note: "Saving the widget saves this dataset too — publication includes the dataset before the chart."
4. "Add to dashboard" Select: "Lab operations" · "HIV/ART program" · "Don't place it yet".
5. Footer button label is derived: "Save widget and add" when a dashboard is chosen, "Save widget" when "Don't place it yet", "Saved" (disabled) after.

### 7. Success toast

Fixed, `right: 1.5rem`, `bottom: 11rem`, `z-index: 150`, `max-width: 24rem`, padding `1rem`, background `#fff`, `border-left: 3px solid #24a148`, `box-shadow: 0 0.125rem 0.5rem rgb(0 0 0 / 20%)`, `role="status"`. Checkmark-filled-20 `#24a148`. Message `0.875rem`/1.5, then a `0.375rem` gap and context-appropriate links such as "Download bundle" · "Open Superset". Auto-dismiss after 6s.

Messages:
- `"<dataset name>" saved to Datasets.`
- `"<widget name>" saved and added to <dashboard name>.`
- `"<widget name>" saved to Widgets. Not placed on a dashboard.`
- `<dashboard name> bundle is ready for Superset import.`

Use Carbon `ToastNotification` if it can be positioned this way; otherwise match the geometry above.

## Interactions & Behavior

**Navigation**
- Nav item click sets the active screen and closes any open panel. Collapse toggle animates nav `width`, and content `margin-left`, composer `left`, and scrim `left` all track it (140ms).
- "New from question" (Datasets) → Ask. "New session" (Ask) → empty thread, cleared composer.

**Draft tile → panel**
- Click anywhere on a tile opens the panel in the matching mode. Panel closes on: close button, "Close", or scrim click. Add `Escape` to close and return focus to the invoking tile (a11y requirement not visible in the prototype).

**Saving**
- *Save dataset*: append an immutable Catalyst Dataset version, mark it Saved, close the panel, and toast. Dataset tile accent `#0f62fe` → `#24a148`; pill Draft → Saved; nav Datasets count +1.
- *Save widget*: if its dataset is unsaved, save its Dataset version first; if a dashboard was chosen, append the Widget version to that Dashboard draft. Mark both Saved, close panel, toast. Nav Widgets count +1; widget meta line becomes "Line chart · on <dashboard>"; the dashboard row shows a pending bundle and its layout mirror shows the new tile in `#0f62fe`.
- Save buttons are single-shot: disabled and relabeled after success. Local unsaved changes may be discarded before publication; cross-system Undo after import is deferred.
- *Publish to Superset* (Dashboards): writes/downloads the atomic outbox ZIP and flips the row to `Bundle ready`. Only an exact CLI receipt may later show `Imported` or `Import failed`.

**Not in the MVP** (all considered and cut, in this order of likely reintroduction): arbitrary column-mapping panel, model-generated/"why this suggestion" reasoning, full Catalyst chart rendering, size/slot picker, parameter → native-filter mapping, config diff before write, embedded Superset viewing, dashboard rename/delete/share from Catalyst.

**Loading / error states to add during implementation** (the prototype does not draw them):
- Question/follow-up in flight: skeleton latest-turn workbench state; composer
  Send disabled with an inline spinner. No Dataset tile exists before a
  successful explicit Run.
- SQL generation failure: inline notification in the thread with the failure reason, raw candidate evidence when available, and a retry action; keep the prior query current and editable.
- Query execution error: the active workbench retains the editable SQL and shows
  the database diagnostic; do not create a Dataset tile for a failed execution.
- Bundle generation failure: drafts stay Saved, no current pointer changes, and an inline notification exposes the bounded diagnostic and retry action.
- Superset CLI import failure: Superset remains available, the prior imported dashboard remains usable, and the exact bundle shows `Import failed` with retry/reset guidance.
- Superset render check pending: schematic thumbnail remains visible while the
  dashboard row reports `Bundle ready`; the authoritative preview is available
  only after a successful import receipt.

**Responsive** — desktop-first (the working surface is a laptop or larger). Below roughly 1024px, force the nav collapsed and let the panel take `calc(100vw - 4rem)`. Below 672px (Carbon's `md`), the panel goes full-screen. Tiles and cards already wrap.

**Motion** — nav width and content offset 140ms; chevrons 140ms (nav) / 110ms (accordion); page blur 120ms. Panel entry should be a 240ms ease-out slide from the right in production. Respect `prefers-reduced-motion` by dropping the blur and the slide.

## State Management

Session-scoped state (prototype names in parentheses):

| State | Type | Purpose / transitions |
| --- | --- | --- |
| `screen` | "Ask" \| "Datasets" \| "Widgets" \| "Dashboards" | nav selection; also closes the panel |
| `navExpanded` | boolean | nav rail collapsed/expanded |
| `thread` | boolean (prototype) / message array (production) | empty vs. populated Ask screen |
| `currentVersion` + `editorSnapshot` | immutable version reference + exact mutable SQL/typed-parameter buffer | drives the latest turn's single active workbench card; dirty/unresolved state is never duplicated in a Dataset draft |
| `validation` + `execution` | exact-digest findings and latest execution summary/typed result reference | drives advisory state, explicit Run, result staleness, and eligibility for a Dataset tile |
| `panel` | null \| "dataset" \| "widget" | which slide-over mode is open |
| `sqlOpen` | boolean | SQL/provenance accordion |
| `dsSaved`, `wSaved` | boolean (prototype) / immutable Catalyst version ids (implementation) | Draft vs. Saved for the current drafts |
| `dsName`, `wName` | string | editable names, deterministically prefilled from question/result metadata |
| `dashboard` | "lab" \| "hiv" \| "none" | placement choice |
| `prompt` | string | composer value |
| `toast` | null \| string | transient confirmation, 6s timer |
| `labPending` | boolean | per-dashboard bundle-ready flag |

In implementation, replace the booleans with server-owned entities: a session/thread resource holding messages and draft objects plus immutable Dataset, Widget, Dashboard, and Bundle Export versions. Stable logical Dashboard UUIDs and version-derived child UUIDs are export provenance; drafts persist server-side so reload does not lose work.

**Data fetching**
- Existing question/turn/version/Validate/Run routes and semantics remain the
  behavioral contract. A question or follow-up produces a complete query for
  manual review in the one canonical editor; execution is always explicit.
- Explicit read-only execution returns typed rows and diagnostics; these feed
  tile metadata, the movable panel preview, and the shape-based viz suggestion.
- Publish: Gateway serializes database → virtual dataset → chart → dashboard YAML plus the Catalyst manifest, writes the content-addressed ZIP and `current.json` atomically to the outbox, and offers the same ZIP for download.
- Import: a one-shot Compose service runs the pinned Superset CLI, reads the outbox read-only, and writes a digest-addressed receipt. It is invoked during clean bootstrap or by the explicit running-instance helper; Catalyst never accesses the Docker socket.
- Libraries read from Catalyst's own records and import receipts, so they render without querying Superset on every page view.

## Design Tokens

Carbon Gray 10 theme. Everything below is already a Carbon token — use the token, not the hex.

**Color**
| Value | Carbon token | Used for |
| --- | --- | --- |
| `#161616` | gray-100 / `$text-primary` | body text, headings |
| `#262626` | gray-90 | demo banner background |
| `#393939` | gray-80 | secondary button |
| `#525252` | gray-70 / `$text-secondary` | descriptions, inactive nav |
| `#6f6f6f` | gray-60 / `$text-helper` | meta, labels |
| `#8d8d8d` | gray-50 | field borders, axes |
| `#c6c6c6` | gray-40 | tile borders, disabled, chart neutral |
| `#e0e0e0` | gray-30 | dividers, user bubble, empty layout tile |
| `#e8e8e8` | gray-20 / `$layer-hover` | table headers, hover, active nav |
| `#f4f4f4` | gray-10 / `$layer` | app background, fields, zebra |
| `#ffffff` | white / `$layer-01` | surfaces |
| `#0f62fe` | blue-60 / `$interactive` | primary action, links, accents, series 1 |
| `#0050e6` | — | primary hover |
| `#0043ce` | blue-70 | link hover |
| `#78a9ff` | blue-40 | proportion segment 2 |
| `#8a3ffc` | purple-60 | AI/suggestion accent, series 2 |
| `#a56eff` | purple-50 | sparkline series 2 |
| `#e8daff` / `#6929c4` | purple-20 / purple-70 | source tag on the query screen |
| `#24a148` / `#defbe6` / `#0e6027` | green-50 / green-10 / green-70 | saved state, success |
| `#f1c21b` / `#fcf4d6` / `#684e00` | yellow-30 / yellow-10 / yellow-70 | draft state, pending accent |
| `#8e6a00` | — | inline warning text |
| `#e5e0df` / `#171414` | warm gray | demo pill (existing app) |

**Type** — IBM Plex Sans (400/500/600) and IBM Plex Mono (400) for SQL, parameters, and identifiers.
| Size | Use |
| --- | --- |
| `2rem` / 400 / `-0.025em` / 1.15 | page H1 |
| `1.25rem` / 400 / `-0.025em` | panel title |
| `1rem` / 600 | object names, KPI-adjacent |
| `1rem` / 400 / 1.5 | composer input |
| `0.875rem` / 400–600 / 1.5 | body, tiles, tables, buttons |
| `0.75rem` / 400–600 | meta, labels, pills, eyebrows (`0.08em`, uppercase) |
| `2rem` / 600 / `-0.02em` | KPI value |

**Spacing** — Carbon scale: `0.125 / 0.25 / 0.375 / 0.5 / 0.75 / 1 / 1.25 / 1.5 / 2 / 3rem`. Cards and tiles use `1rem`–`1.5rem` padding; stacks use `gap: 1rem`; grids `gap: 1rem`.

**Radius** — 0 everywhere except status pills (`0.75rem`, i.e. fully round at `1.5rem` height) and the demo pill (`0.5625rem`).

**Shadow** — surfaces `0 0.125rem 0.5rem rgb(0 0 0 / 8%)`; composer `0 -0.25rem 1rem rgb(0 0 0 / 18%)`; panel `-0.25rem 0 1rem rgb(0 0 0 / 18%)`; toast `0 0.125rem 0.5rem rgb(0 0 0 / 20%)`.

**Borders** — hairlines `1px solid #e0e0e0`; interactive edges `1px solid #c6c6c6` (hover `#0f62fe`); field underline `1px solid #8d8d8d`; state accents `3px` left; composer top `4px solid #0f62fe`.

**Z-index** — banner 120 · nav 100 · composer 90 · scrim 130 · panel 140 · toast 150.

## Assets

No image assets. All icons are inline SVG on Carbon's 32×32 grid, drawn from `@carbon/icons-react`: WarningFilled, ChevronLeft, ChevronRight, Chat, DataTable, ChartLine (nav "Widgets" uses a chart glyph), Dashboard, Add, ArrowRight, Close, CheckmarkFilled, Launch. Replace the inline paths with the real icon components. Charts and thumbnails are hand-drawn SVG placeholders standing in for the production chart renderer.

Fonts: IBM Plex Sans and IBM Plex Mono, loaded from Google Fonts in the prototype — use the app's existing `@ibm/plex` dependency instead.

## Files

In this bundle:

| File | What it is |
| --- | --- |
| `Catalyst Dashboard Builder 4c.dc.html` | **The design to implement.** Hi-fi, interactive: all four screens, both Ask states, both panel modes, save flows, toast. Open it and click through before starting. |
| `Catalyst Query Screen.dc.html` | Recreation of today's Catalyst query screen — the visual baseline the new work must sit alongside. |
| `Dashboard Builder Wireframes.dc.html` | Lo-fi rationale: four structural directions (1a–1d), the merged direction (2a–2c), the minimal conversational variants (3a–3b), and the panel variants (4a–4c). 4c is the one that was built. |
| `github.md` | Repo association and screen map. |

These are `.dc.html` files — self-contained HTML that opens directly in a browser. Ignore the `support.js` runtime and the `<x-dc>` wrapper; the design content is the markup and the small state class at the bottom of each file.

Source files the recreation was based on, in `DIGI-UW/openelis-catalyst` @ `main`: `catalyst-ui/src/styles.css`, `catalyst-ui/src/App.tsx`, `catalyst-ui/src/features/query/QueryWorkspace.tsx`, and `catalyst-ui/src/features/query/components/{DemoBanner,AskOpenElisNavigation,DatasetBrowser,QuestionForm,ResultsTable,ProvenancePanel}.tsx`, plus `analytics/catalog/analytics-catalog-v1.json` for schema and column semantics.

## Open questions for the team

1. **Superset-only edits** — one-way MVP publication replaces direct layout edits; bidirectional export/reconciliation remains an API-phase decision.
2. **Orphan cleanup** — version-derived children accumulate by design; the MVP uses an explicit local reset, while selective cleanup is deferred.
3. **Dataset naming collisions** — deterministic version-addressed names avoid collisions in the MVP; production-friendly display/retention policy remains open.
4. **Who can publish** — the unauthenticated local demo shows the action unconditionally; role-gating belongs to production authorization.
5. **Refresh cadence** — the local MVP uses Superset defaults; explicit cache policy remains a later operational decision.
