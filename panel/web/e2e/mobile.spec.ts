import { expect, test } from "@playwright/test";

test("login and invite pages fit a phone screen", async ({ page }) => {
  for (const path of ["/login", "/invite"]) {
    await page.goto(path);
    await expect(page.getByRole("heading").first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${path} scrolls horizontally`).toBeLessThanOrEqual(0);
  }
});
