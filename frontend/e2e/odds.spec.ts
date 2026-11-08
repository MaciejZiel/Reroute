import { expect, test } from "@playwright/test";
import { mockApi, vehicleDetail } from "./mock";

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`the chance-of-a-delay tab finds a stop, quotes the odds and maps the hours (${viewport.width}px)`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => localStorage.setItem("reroute-language", "pl"));
    // A fixed clock: Monday 8:30 in Warsaw.
    await page.clock.setFixedTime(new Date("2026-11-09T07:30:00Z"));
    await mockApi(page, () => vehicleDetail({}));
    await page.goto("/");

    await page.getByRole("button", { name: "Szansa spóźnienia" }).click();
    const panel = page.getByRole("region", { name: "Szansa spóźnienia" });
    await expect(panel.locator("h2")).toBeFocused();
    await panel.getByRole("combobox").fill("Rondo");
    await panel.locator(".search-results li").first().click();

    const card = panel.locator(".odds-card");
    await expect(card).toContainText("Teraz: dzień roboczy, 8:00–8:59");
    await expect(card).toContainText("1 na 3"); // 8:00 weekday: 35% more than 3 min late
    await expect(card).toContainText("Z 40 nagranych przyjazdów w 18 dniach.");
    await expect(panel.locator(".odds-cell")).toHaveCount(3 * 20);
    await page.screenshot({ path: `test-results/odds-${viewport.width}.png` });

    await panel.getByRole("button", { name: /^sobota 10:00–10:59/ }).click();
    await expect(card).toContainText("Wybrane: sobota, 10:00–10:59");
    await expect(card).toContainText("1 na 17");
    await panel.getByRole("button", { name: /^niedziela 9:00–9:59/ }).click();
    await expect(card).toContainText("Za mało nagranych przyjazdów o tej porze (3)");

    await panel.locator("select").selectOption({ label: "10 → Łazienki (słupek 02)" });
    await expect(panel.locator(".odds-cell")).toHaveCount(3 * 19);
    await expect(card).toContainText("Teraz: dzień roboczy, 8:00–8:59"); // a new line starts at now
    await expect(card).toContainText("1 na 2");
    expect(await panel.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(false);
  });
}
