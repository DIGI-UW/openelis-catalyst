import { expect, test, type Page } from "@playwright/test";
import { installBaselineApi } from "./support/baseline-fixture";

/*
 * catalyst#35, in a real browser.
 *
 * The composer is a scroll-driven disclosure with three modes. Scrolling up to
 * the lip is a documented affordance, so "the mode never changes" is the wrong
 * assertion — what must hold is that *jitter* changes nothing: wheel deltas
 * below the accumulated-intent gate, straddling the full/line boundary, leave
 * the mode where it was. A short viewport against the fixture's long thread is
 * what makes the boundary reachable at all.
 *
 * The unit test in TurnNotebook.test.tsx drives the same state machine
 * directly and was verified to fail when the intent gate is removed; this one
 * covers the parts jsdom cannot: real wheel events, real layout, real
 * requestAnimationFrame batching.
 */

const mode = (page: Page) =>
  page.locator("#refine-openelis").getAttribute("data-mode");

test("the composer does not flicker while scroll jitters", async ({ page }) => {
  await installBaselineApi(page);
  // Short enough that the thread scrolls well past a screen.
  await page.setViewportSize({ width: 1280, height: 620 });
  await page.goto("/");
  await expect(page.locator(".query-turn__dataset").first()).toBeVisible();

  // String form, like the other specs: the e2e tsconfig has no DOM lib.
  const scrollable = await page.evaluate<number>(
    "document.documentElement.scrollHeight - window.innerHeight",
  );
  expect(scrollable).toBeGreaterThan(400);

  await page.mouse.move(640, 300);
  // Land at the end deliberately, then step up off the boundary. Both are real
  // gestures; whatever they settle on is the baseline jitter must preserve.
  await page.evaluate(
    "window.scrollTo({ top: document.documentElement.scrollHeight })",
  );
  await page.waitForTimeout(400);
  await page.mouse.wheel(0, -140);
  await page.waitForTimeout(400);
  const baseline = await mode(page);
  expect(baseline).not.toBeNull();

  const samples: (string | null)[] = [];
  for (const delta of [8, -8, 12, -12, 6, -6, 10, -10, 14, -14]) {
    await page.mouse.wheel(0, delta);
    await page.waitForTimeout(160);
    samples.push(await mode(page));
  }

  expect(samples).toEqual(samples.map(() => baseline));
});

test("all three composer modes stay reachable", async ({ page }) => {
  await installBaselineApi(page);
  await page.setViewportSize({ width: 1280, height: 620 });
  await page.goto("/");
  await expect(page.locator(".query-turn__dataset").first()).toBeVisible();
  await page.mouse.move(640, 300);

  const seen = new Set<string>();
  const sample = async () => {
    const value = await mode(page);
    if (value) seen.add(value);
  };

  // Down to now: full. Small step up: line. Far up into history: tucked.
  await page.mouse.wheel(0, 2000);
  await page.waitForTimeout(400);
  await sample();
  await page.mouse.wheel(0, -380);
  await page.waitForTimeout(400);
  await sample();
  await page.mouse.wheel(0, -2000);
  await page.waitForTimeout(500);
  await sample();

  expect([...seen].sort()).toEqual(["full", "line", "tucked"]);
});
