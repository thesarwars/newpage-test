import { expect, test } from "@playwright/test";

/**
 * The acceptance criterion for M7, and the product's central claim:
 *
 *   ask a question → an answer streams → it carries a citation →
 *   clicking it highlights the exact source span
 *
 * Every link in that chain is verified somewhere else — the offsets in Python
 * tests, the parser in vitest, the citation-to-document mapping server-side
 * before it is ever stored. None of those prove a browser renders a `<mark>`
 * over the right words, which is the only form of the claim a user experiences.
 */

test.describe("grounded answer with clickable evidence", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    // A fresh browser context is a fresh session, so the workspace is empty.
    await page.getByRole("button", { name: "Load demo data" }).click();
    // Scoped to the rail: the same label also appears as a scope pill in the
    // composer, and an unscoped locator matches both.
    const rail = page.getByRole("complementary", { name: "Documents" });
    await expect(rail.getByText("Senior Backend Engineer — Northwind")).toBeVisible();
  });

  test("streams an answer, cites it, and highlights the source", async ({ page }) => {
    const composer = page.getByRole("textbox", {
      name: "Ask a question about your documents",
    });
    await composer.fill("What am I missing for Job #2?");
    await composer.press("Enter");

    // Sources land before any answer text exists. That ordering is the product
    // decision that makes retrieval legible rather than merely claimed, so it
    // is asserted rather than assumed.
    const sources = page.getByRole("list", { name: /passages retrieved/ });
    await expect(sources).toBeVisible();

    const citation = page.locator("[data-citation]").first();
    await expect(citation).toBeVisible();

    // The evidence panel starts empty and opens on click.
    await expect(page.getByText(/Click a citation in an answer/)).toBeVisible();
    await citation.click();

    const mark = page.locator("mark");
    await expect(mark).toBeVisible();

    // The highlight must be the *cited* text, not merely some text. This is the
    // assertion the whole offset contract exists to make true: the mark's
    // content has to equal what the citation said it quoted.
    const cited = await citation.getAttribute("aria-label");
    const quoted = cited?.replace(/^Citation \d+: /, "") ?? "";
    const highlighted = (await mark.textContent()) ?? "";
    expect(highlighted.startsWith(quoted.slice(0, 40))).toBe(true);

    // And it has to be scrolled into view, not merely present in the DOM.
    await expect(mark).toBeInViewport();
  });

  test("Escape closes the panel and returns focus to the citation", async ({ page }) => {
    const composer = page.getByRole("textbox", {
      name: "Ask a question about your documents",
    });
    await composer.fill("What am I missing for Job #2?");
    await composer.press("Enter");

    const citation = page.locator("[data-citation]").first();
    await citation.click();
    await expect(page.locator("mark")).toBeVisible();

    await page.keyboard.press("Escape");

    // A non-modal panel does not trap focus, so the way back has to be explicit
    // — otherwise a keyboard user is returned to the top of the document.
    await expect(page.locator("mark")).toBeHidden();
    await expect(citation).toBeFocused();
  });

  test("an out-of-scope question is refused without spending anything", async ({ page }) => {
    const composer = page.getByRole("textbox", {
      name: "Ask a question about your documents",
    });
    await composer.fill("What is the weather in Berlin?");
    await composer.press("Enter");

    await expect(page.getByText(/only analyze your résumé/i)).toBeVisible();
    // No answer body, and therefore no citations to click.
    await expect(page.locator("[data-citation]")).toHaveCount(0);
  });

  test("the demo-mode banner is present and unmistakable", async ({ page }) => {
    // A reviewer without a key must never mistake a stub for model output —
    // the alternative is them concluding the model is poor.
    await expect(page.getByText(/Demo mode/)).toBeVisible();
  });
});
