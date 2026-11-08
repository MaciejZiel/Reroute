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
      await expect(page.locator(".panel h2")).toContainText("Rondo Daszyńskiego");
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
  test(`the why line names its stop, follows the UI language and shows a precise range (${language})`, async ({ page }) => {
    await page.addInitScript((lang) => localStorage.setItem("reroute-language", lang), language);
    const summary = language === "pl" ? "Teraz pojazd jest opóźniony o 1 min 29 s." : "Now 1 min 29 s late.";
    await mockApi(page, (url) => vehicleDetail({
      model_status: "model", lower_delay_seconds: 244, upper_delay_seconds: 305,
      why: { summary: url.searchParams.get("lang") === language ? summary : "WRONG LANGUAGE REQUESTED", factors: [] },
    }));
    await page.goto("/");
    await openVehicle(page);

    const why = page.locator(".why-note");
    await expect(why).toContainText("Rondo ONZ");
    await expect(why).toContainText(summary);
    await expect(why.locator("[lang]")).toHaveAttribute("lang", language);
    await expect(why).toContainText("+4 min 4 s");
    await expect(why).toContainText("+5 min 5 s");
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
    await expect(page.locator(".why-note").filter({ hasText: "Rondo ONZ" })).toHaveCount(0);
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
}

test("a stop picked with the keyboard moves focus to the panel, and the next Tab stays in it", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  await expect(page.locator("body")).toBeFocused();

  const box = page.getByRole("combobox");
  await box.fill("Rondo");
  await expect(page.locator(".search-results li").first()).toBeVisible();
  await box.press("Enter");

  const heading = page.locator(".panel h2");
  await expect(heading).toBeFocused();
  await expect(heading).toContainText("Rondo");
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => Boolean(document.activeElement?.closest(".panel")))).toBe(true);
});

test("a stop opened earlier is offered again on the start panel", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  await page.getByRole("combobox").fill("Rondo");
  await page.locator(".search-results li").first().click();
  await expect(page.locator(".panel h2")).toContainText("Rondo Daszyńskiego");
  await page.locator(".panel-close").click();

  const recent = page.locator(".recent-row");
  await expect(recent).toHaveCount(1);
  await expect(recent).toContainText("Rondo 1");
  await recent.click();
  await expect(page.locator(".panel h2")).toContainText("Rondo Daszyńskiego");
});

test("a departure shows the model's arrival window only where the model answered", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.addInitScript(() => localStorage.setItem("reroute-language", "pl"));
  await page.goto("/");
  await page.getByRole("combobox").fill("Rondo");
  await page.locator(".search-results li").first().click();
  await page.locator(".line-row").first().click();
  const rows = page.locator(".departure-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).locator(".forecast")).toHaveText(/14:06.14:09/);
  await expect(rows.nth(0).locator(".delay-badge")).toHaveText("+2 min");
  await expect(rows.nth(1).locator(".forecast")).toHaveCount(0);
  await expect(rows.nth(1)).toContainText("14:10");
  await expect(rows.nth(1).locator(".delay-badge")).toHaveText("na czas");
  await expect(rows.nth(2).locator(".delay-badge")).toHaveText("rozkład");
});

test("a stop lists each direction of a line on its own row, in a fixed order", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.addInitScript(() => localStorage.setItem("reroute-language", "pl"));
  await page.goto("/");
  await page.getByRole("combobox").fill("Rondo");
  await page.locator(".search-results li").first().click();
  const rows = page.locator(".line-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("→ Koniec");
  await expect(rows.nth(1)).toContainText("→ Łazienki");
  await expect(page.locator(".panel-sub")).toHaveText("0 słupków · 1 linia kursuje");
});

test("Enter right after typing waits for the results of the typed text", async ({ page }) => {
  await mockApi(page, () => vehicleDetail({}));
  await page.goto("/");
  const box = page.getByRole("combobox");
  await box.fill("Rondo");
  await expect(page.locator(".search-results li").first()).toContainText("Rondo 1");
  await box.fill("Slow");
  await box.press("Enter");
  await expect(page.locator(".panel h2")).toContainText("Rondo Daszyńskiego"); // departures mock name
  expect(await page.evaluate(() => localStorage.getItem("reroute-recent-stops") ?? "")).toContain("Slow 1");
});
