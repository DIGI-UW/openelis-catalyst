/**
 * The visual baseline — Checkpoint 1 of the styling roadmap.
 *
 * Every later checkpoint is measured with this. It exists so a token
 * substitution or a theme change can be reviewed as a diff of named surfaces
 * rather than trusted, and so that when a surface moves you can say which
 * change moved it.
 *
 * Run it with `npm run baseline`. Accept intended changes with
 * `npm run baseline -- --update-snapshots`, and say in the pull request which
 * surfaces moved and why.
 *
 * It runs only against the deterministic mock: no models, no database, no
 * clock. Snapshots are platform-suffixed by Playwright, so a set generated on
 * one operating system never silently grades another.
 */
import { expect, test } from "@playwright/test";
import { installBaselineApi } from "./support/baseline-fixture";

/*
  No pixel tolerance, and no per-pixel threshold either.

  Both defaults were measured to be blind, in turn. A 1% pixel ratio missed a
  recoloured 3px border (0.26% of a full-page shot). Removing it left
  Playwright's default `threshold: 0.2`, which compares pixels perceptually --
  and #ffffff against #f4f4f4 is roughly a 4% distance, so an entire surface
  can swap between near-whites and be reported identical. That is exactly the
  change a token migration between neutral greys makes, so the instrument was
  blind to the one thing it was built to watch.

  Verified the hard way: it reported 11/11 while the rail and its nav had
  swapped colours on screen.
*/
const shot = {
  animations: "disabled" as const,
  // A blinking caret is not a design change; it moved 2% of the editor shot.
  caret: "hide" as const,
  threshold: 0,
  /*
    An absolute two-pixel allowance, not a ratio. A ratio scales with the
    image and is how the first two versions of this went blind. Two pixels
    cannot conceal anything this instrument exists to catch -- the surface
    inversion it missed moved tens of thousands -- but it does absorb the
    single antialiased pixel on an element boundary that otherwise makes the
    whole set flap.
  */
  maxDiffPixels: 2,
};

const THEMES = ["light", "dark"] as const;

/** Pin the preference before the app boots, so no flash of the other theme. */
const useTheme = async (page: import("@playwright/test").Page, theme: string) => {
  await page.addInitScript(
    `window.localStorage.setItem("catalyst.theme", ${JSON.stringify("PLACEHOLDER")})`.replace(
      "PLACEHOLDER",
      theme,
    ),
  );
};

for (const theme of THEMES) {
test.describe(`visual baseline (${theme})`, () => {
  test("empty session", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page, { empty: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await expect(page.getByLabel("Question")).toBeVisible();
    await expect(page).toHaveScreenshot(`empty-session-${theme}.png`, {
      ...shot,
      fullPage: true,
    });
  });

  test("thread with a run, a failure and a repair", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    // The newest cell is open on arrival and carries its dataset.
    await expect(page.locator(".query-turn__dataset").first()).toBeVisible();
    await expect(page).toHaveScreenshot(`thread-${theme}.png`, { ...shot, fullPage: true });
  });

  test("a failed run's cell", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    // Cell [2] is the run the database rejected; open it.
    await page.getByRole("button", { name: /Query turn 2/ }).click();
    await expect(page.getByText('column "test_type" does not exist')).toBeVisible();
    await expect(page.locator("#turn-2")).toHaveScreenshot(`failed-run-${theme}.png`, shot);
  });

  test("expanded dataset tile", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const tile = page.locator(".query-turn__dataset").first();
    await expect(tile).toBeVisible();
    await expect(tile).toHaveScreenshot(`dataset-tile-${theme}.png`, shot);
  });

  test("dataset review dialog", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page
      .locator(".query-turn__dataset")
      .first()
      .getByRole("button", { name: "Save to datasets" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Review panel" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveScreenshot(`review-dialog-${theme}.png`, shot);
  });

  for (const section of ["Datasets", "Widgets", "Dashboards"] as const) {
    test(`${section.toLowerCase()} library`, async ({ page }) => {
      await useTheme(page, theme);
      await installBaselineApi(page);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto("/");
      const navButton = page
        .getByRole("complementary", { name: "Catalyst" })
        .getByRole("navigation", { name: "Sections" })
        .getByRole("button", { name: section });
      await navButton.click();
      // Prove the section actually took before shooting it.
      await expect(navButton).toHaveAttribute("aria-current", "page");
      await expect(page.getByRole("heading", { level: 1, name: section })).toBeVisible();
      // The heading renders before the library has loaded, and the loading
      // line takes up space while it does. Wait for the settled state, or the
      // shot lands mid-race and the baseline differs from run to run.
      await expect(page.getByText(`No ${section} saved yet.`, { exact: true }))
        .toBeVisible();
      await expect(page.getByText("Loading library…")).toHaveCount(0);
      // Scoped to the library itself, not the whole page: the refine composer
      // is still mounted on these screens and its scroll-adaptive height
      // changes the document height between runs, which a full-page shot
      // records as a difference. (That the composer appears here at all is a
      // separate finding, recorded in the follow-through goals.)
      await expect(page.locator(".builder-library")).toHaveScreenshot(`library-${section.toLowerCase()}-${theme}.png`,
        shot,
      );
    });
  }

  // States CP-3 changed and nothing was watching: the editor's own chrome, a
  // selected turn in the rail, and the thread's status colours side by side.
  test("open editor", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "Edit query" }).click();
    const editor = page.locator(".workbench-panel");
    await expect(editor).toBeVisible();
    await expect(page.getByRole("textbox", { name: "SQL query" })).toBeVisible();
    await expect(editor).toHaveScreenshot(`editor-${theme}.png`, shot);
  });

  test("rail turns, with one selected", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const rail = page.getByRole("complementary", { name: "Catalyst" });
    const turns = rail.getByRole("button", { name: /^TURNS/ });
    await expect(turns).toHaveAttribute("aria-expanded", "true");
    // Succeeded, failed and not-run dots in one shot.
    await expect(rail.locator(".workbench-rail__turns")).toHaveScreenshot(`rail-turns-${theme}.png`,
      shot,
    );
  });

  test("thread statuses side by side", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    // Collapse the newest cell so all three headers show their status at once.
    await page.getByRole("button", { name: /Query turn 3/ }).click();
    const timeline = page.locator(".turn-notebook__timeline");
    await expect(timeline).toBeVisible();
    await expect(timeline).toHaveScreenshot(`thread-statuses-${theme}.png`, shot);
  });

  // A phone. The rail is a bar here rather than a column, and it was 398px of
  // a 664px screen before it collapsed behind a disclosure, so this is the
  // state most worth holding still.
  test("stacked on a phone", async ({ page }) => {
    await useTheme(page, theme);
    await installBaselineApi(page);
    await page.setViewportSize({ width: 390, height: 664 });
    await page.goto("/");
    const rail = page.getByRole("complementary", { name: "Catalyst" });
    await expect(rail).toHaveAttribute("data-stacked", "true");
    await expect(page.getByRole("button", { name: "Menu" })).toBeVisible();
    await expect(page).toHaveScreenshot(`stacked-${theme}.png`, {
      ...shot,
      fullPage: false,
    });
  });

  // The rail is resizable, and its catalog and nav both adapt to the width —
  // the labels hide near the minimum, the catalog gains columns near the top.
  for (const [name, width] of [
    ["min", 200],
    ["default", 240],
    ["wide", 520],
  ] as const) {
    test(`rail at ${name} width`, async ({ page }) => {
      await useTheme(page, theme);
      await installBaselineApi(page);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto("/");
      const rail = page.getByRole("complementary", { name: "Catalyst" });
      await expect(rail).toBeVisible();
      await rail.getByRole("button", { name: /^DATA/ }).click();
      // Set the width the way the resize handle does — through the custom
      // property the shell and the rail both read — rather than reaching into
      // the DOM, which would need browser typings this project does not give
      // its end-to-end sources.
      await page.addStyleTag({
        content:
          `.dashboard-builder-shell{--dashboard-nav-width:${width}px}` +
          `.workbench-rail{width:${width}px}`,
      });
      await expect(rail).toHaveScreenshot(`rail-${name}-${theme}.png`, shot);
    });
  }
});
}
