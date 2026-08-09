import { expect, type Page } from "@playwright/test";

/*
 * Reveal the follow-up composer before typing into it.
 *
 * The composer is a scroll-driven disclosure: it renders `data-mode="tucked"`
 * until the page is scrolled to the bottom, and a completed run closes the
 * editor, so after a query executes the follow-up textbox is not in the DOM.
 * A spec that types straight into it only passes by winning a race against
 * React -- which the demo specs did, until dwelling on the result long enough
 * to actually film it let the collapse land first.
 *
 * The composer has three modes -- full, line, tucked -- and scrolling to the
 * bottom is NOT a reliable way to reach full: the mode only changes on
 * accumulated scroll intent, and a completed run has already auto-scrolled to
 * reveal its result, so window.scrollTo fires no event and the mode stays
 * "line". Clicking the restore toggle is deterministic, and it is the
 * affordance a person actually uses.
 */
export const openComposer = async (page: Page): Promise<void> => {
  const composer = page.locator("#refine-openelis");
  if ((await composer.getAttribute("data-mode")) === "full") return;
  await page.locator("#refine-openelis-toggle").click();
  await expect(composer).toHaveAttribute("data-mode", "full");
};
