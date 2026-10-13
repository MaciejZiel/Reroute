import { expect, test } from "@playwright/test";
import { mockApi, vehicleDetail } from "./mock";

const LANGUAGES = ["pl", "en"] as const;
const VIEWPORTS = [{ width: 1440, height: 900 }, { width: 400, height: 800 }];

for (const language of LANGUAGES) {
  for (const viewport of VIEWPORTS) {
    test(`the model panel states its limits and the unseen-data numbers (${language}, ${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
      await mockApi(page, () => vehicleDetail({}));
      await page.goto("/");

      await page.getByRole("button", { name: language === "pl" ? "O modelu" : "About the model" }).click();
      const panel = page.getByRole("region", { name: language === "pl" ? "O modelu" : "About the model" });
      await expect(panel).toBeVisible();
      await expect(panel).toContainText("20261013-f2a7a97f");
      // one evening, said up front and again in the list of what it does not know
      await expect(panel).toContainText(language === "pl" ? "jednego wieczoru" : "one evening");
      await expect(panel).toContainText(language === "pl" ? "40,6 s" : "40.6 s");
      await expect(panel).toContainText(language === "pl" ? "79,9%" : "79.9%");
      // the window the model was tuned on is not shown as evidence
      await expect(panel).not.toContainText(language === "pl" ? "81,0%" : "81.0%");
      // nothing overflows sideways
      const overflow = await panel.evaluate((element) => element.scrollWidth > element.clientWidth);
      expect(overflow).toBe(false);

      await panel.getByRole("button", { name: language === "pl" ? "Zamknij" : "Close" }).click();
      await expect(panel).toHaveCount(0);
    });
  }
}
