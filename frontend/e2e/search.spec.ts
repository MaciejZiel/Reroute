import { expect, test } from "@playwright/test";
import { mockApi, vehicleDetail } from "./mock";

const LANGUAGES = ["pl", "en"] as const;
const VIEWPORTS = [{ width: 1440, height: 900 }, { width: 400, height: 800 }];

for (const language of LANGUAGES) {
  for (const viewport of VIEWPORTS) {
    test(`a mouse click on the first search result picks it (${language}, ${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
      await mockApi(page, () => vehicleDetail({}));
      await page.goto("/");

      const box = page.getByRole("combobox");
      await box.fill("Rondo");
      const first = page.locator(".search-results li").first();
      await expect(first).toBeVisible();

      // The element under the centre of every result must be the result itself, not a card that
      // paints over the dropdown (the cause of the click that used to miss)...
      const covered = await page.$$eval(".search-results li", (items) => items.filter((item) => {
        const rect = item.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return !hit || !item.contains(hit);
      }).length);
      expect(covered).toBe(0);

      // ...and a real mouse click (Playwright refuses to click an element another one covers).
      await first.click();
      await expect(box).toHaveValue("");
      await expect(page.locator(".search-results")).toHaveCount(0);
      await expect(page.locator(".panel")).toContainText("Rondo 1");
    });
  }
}

async function openVehicle(page: import("@playwright/test").Page) {
  await page.getByRole("combobox").fill("Rondo");
  await page.locator(".search-results li").first().click();
  await page.getByRole("button", { name: /^9/ }).first().click();
  await page.getByRole("button", { name: /3023/ }).first().click();
}

for (const language of LANGUAGES) {
  test(`the forecast card names its stop, follows the UI language and shows the likely times (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    const summary = language === "pl" ? "Teraz pojazd jest opóźniony o 1 min 29 s." : "Now 1 min 29 s late.";
    await mockApi(page, (url) => vehicleDetail({
      model_status: "model", lower_delay_seconds: 244, upper_delay_seconds: 305,
      why: { summary: url.searchParams.get("lang") === language ? summary : "WRONG LANGUAGE REQUESTED", factors: [] },
    }));
    await page.goto("/");
    await openVehicle(page);

    const card = page.locator(".forecast-card");
    await expect(card).toContainText("Rondo ONZ");
    await expect(card).toContainText(summary);  // no factors: the summary is shown instead
    await expect(card.locator("[lang]")).toHaveAttribute("lang", language);
    await expect(card.locator(".forecast-range")).toHaveText(
      language === "pl" ? /^8 na 10 razy między \d\d:\d\d a \d\d:\d\d$/ : /^8 times out of 10 between \d\d:\d\d and \d\d:\d\d$/);
    // 1 min 29 s late is "on time" on the badge: the badge says how late and why it counts as on time.
    const badge = page.locator(".delay-badge.big");
    await expect(badge).toContainText("1 min 29 s");
    await expect(badge).toHaveAttribute("title", language === "pl" ? /Do 3 minut/ : /Up to 3 minutes/);
  });

  test(`a paused model shows a calm note instead of dropping the line (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    await mockApi(page, () => vehicleDetail({ model_status: "paused", pause_reason: "vehicle_report", why: null }));
    await page.goto("/");
    await openVehicle(page);

    const note = page.locator(".why-paused");
    await expect(note).toBeVisible();
    await expect(note).toContainText(language === "pl" ? "Prognoza na żywo wstrzymana" : "Live prediction paused");
    await expect(page.locator(".forecast-card")).toHaveCount(0);
  });

  test(`a baseline answer for a stop the vehicle has already reached is labelled with a calm note (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    await mockApi(page, () => vehicleDetail({ model_status: "passed", why: null }));
    await page.goto("/");
    await openVehicle(page);

    const note = page.locator(".why-paused");
    await expect(note).toBeVisible();
    await expect(note).toContainText(language === "pl" ? "Brak prognozy modelu" : "No model prediction");
  });

  test(`departures say whether a time is the model's forecast, an estimate or the timetable (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    await mockApi(page, () => vehicleDetail({}));
    await page.goto("/");
    await page.getByRole("combobox").fill("Rondo");
    await page.locator(".search-results li").first().click();
    await page.getByRole("button", { name: /^9/ }).first().click();

    const rows = page.locator(".departure-row");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText(language === "pl" ? "prognoza" : "forecast");
    await expect(rows.nth(1)).toContainText(language === "pl" ? "szacunek" : "estimate");
    await expect(rows.nth(2)).not.toContainText(language === "pl" ? "prognoza" : "forecast");
    await expect(rows.nth(2)).toContainText(language === "pl" ? "rozkład" : "timetable");
  });

  test(`the forecast card lists what moved the number, later in red and earlier in blue (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    await mockApi(page, () => vehicleDetail({
      model_status: "model", lower_delay_seconds: 60, upper_delay_seconds: 150,
      why: { summary: "s", factors: [
        { feature: "leader_delay_s", effect_seconds: 14, text: "Vehicle ahead was late" },
        { feature: "distance", effect_seconds: -42, text: "One stop ahead" },
        { feature: "trend_s_per_min", effect_seconds: 0.2, text: "Rounds to nothing" },
      ] },
    }));
    await page.goto("/");
    await openVehicle(page);

    const factors = page.locator(".forecast-factors li");
    await expect(factors).toHaveCount(2);
    await expect(factors.nth(0)).toContainText("+14 s");
    await expect(factors.nth(0).locator("b")).toHaveClass(/later/);
    await expect(factors.nth(1)).toContainText("−42 s");
    await expect(factors.nth(1).locator("b")).toHaveClass(/earlier/);
    await expect(page.locator(".forecast-summary")).toHaveCount(0);
  });
}
