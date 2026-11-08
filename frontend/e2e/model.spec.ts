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
      // how little data, said up front
      await expect(panel).toContainText(language === "pl" ? "nagranych dniach" : "recorded days");
      await expect(panel).toContainText(language === "pl" ? "40,6 s" : "40.6 s");
      await expect(panel).toContainText(language === "pl" ? "79,9%" : "79.9%");
      // "Trained on" is the training window only; the unseen minutes are listed on their own line
      const trainedOn = panel.locator("dd").filter({ hasText: "16:27" });
      await expect(trainedOn).toContainText("16:27–21:14");
      await expect(trainedOn).not.toContainText("22:09");
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

test("Escape closes the model panel and gives the focus back to its button", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("reroute-language", "pl"));
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  const opener = page.getByRole("button", { name: "O modelu" });
  await opener.click();
  const panel = page.getByRole("region", { name: "O modelu" });
  await expect(panel.locator("h2")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(opener).toBeFocused();
});
