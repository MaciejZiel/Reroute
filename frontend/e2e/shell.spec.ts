import { expect, test } from "@playwright/test";
import { mockApi, vehicleDetail } from "./mock";

const LANGUAGES = ["pl", "en"] as const;
const VIEWPORTS = [{ width: 1440, height: 900 }, { width: 400, height: 800 }];
const COPY = {
  pl: { title: "Nie udało się załadować sieci", retry: "Spróbuj ponownie", doc: /tramwaje i autobusy/ },
  en: { title: "The network did not load", retry: "Try again", doc: /live Warsaw trams and buses/ },
};

for (const language of LANGUAGES) {
  for (const viewport of VIEWPORTS) {
    test(`a failed network request shows a message and a working retry (${language}, ${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
      await mockApi(page, () => vehicleDetail({}));
      let fail = true;
      await page.route("**/api/network", (route) => fail ? route.abort() : route.fallback());
      await page.goto("/");

      const alert = page.locator(".map-error");
      await expect(alert).toContainText(COPY[language].title);
      await expect(page.locator(".map-loading")).toHaveCount(0);
      // The search stays reachable, and the retry target is large enough to tap.
      await expect(page.getByRole("combobox")).toBeEnabled();
      const retry = alert.getByRole("button", { name: COPY[language].retry });
      expect((await retry.boundingBox())?.height).toBeGreaterThanOrEqual(44);

      fail = false;
      await retry.click();
      await expect(alert).toHaveCount(0);
    });
  }

  test(`the page declares its title, one h1 and a main landmark in the UI language (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    await mockApi(page, () => vehicleDetail({}));
    await page.goto("/");
    await expect(page).toHaveTitle(COPY[language].doc);
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("main")).toHaveCount(1);
  });
}

test("the search comes before the map in tab order and touch targets are 44px at 400px", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  const order = await page.evaluate(() => {
    const focusable = [...document.querySelectorAll<HTMLElement>("input, button, [tabindex]:not([tabindex='-1'])")];
    return { search: focusable.findIndex((el) => el.getAttribute("role") === "combobox"), map: focusable.findIndex((el) => el.classList.contains("map-canvas")) };
  });
  expect(order.search).toBeGreaterThanOrEqual(0);
  if (order.map >= 0) expect(order.search).toBeLessThan(order.map);
  for (const locator of [page.locator(".mode-chip"), page.locator(".language-toggle")]) {
    for (const box of await Promise.all((await locator.all()).map((l) => l.boundingBox()))) expect(box?.height).toBeGreaterThanOrEqual(44);
  }
});
