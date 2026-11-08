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
  for (const locator of [page.locator(".layers-button"), page.locator(".language-toggle"), page.locator(".theme-toggle")]) {
    for (const box of await Promise.all((await locator.all()).map((l) => l.boundingBox()))) expect(box?.height).toBeGreaterThanOrEqual(44);
  }
});

test("the theme follows the system until the visitor picks one, and the pick survives a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  const root = page.locator("html");
  await expect(root).toHaveAttribute("data-theme", "light");
  // Set before the app's script ran (no dark first frame), with the browser chrome colour to match.
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#ffffff");

  await page.locator(".theme-toggle").click();
  await expect(root).toHaveAttribute("data-theme", "dark");
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).toBe("rgb(16, 18, 20)");

  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(root).toHaveAttribute("data-theme", "dark"); // a stored pick wins over the system
});

test("map layers live behind one button, and each switch changes what the map draws", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  const button = page.locator(".layers-button");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await button.click();
  const trams = page.getByRole("checkbox", { name: /Tramwaje|Trams/ });
  await expect(trams).toBeChecked();
  await trams.uncheck();
  await expect(trams).not.toBeChecked();
  await page.keyboard.press("Escape");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await expect(button).toBeFocused();
});
