/* Local headless browser only. No user browser/profile is accessed. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1040 }, colorScheme: "dark" });
    const page = await context.newPage(), errors = [];
    page.on("pageerror", e => errors.push(e.message));
    const base = process.env.TEST_URL || "http://127.0.0.1:8790";
    const out = path.resolve("test-results");
    await fs.mkdir(out, { recursive: true });
    await page.goto(base);
    const button = page.getByRole("button", { name: "Светлая тема", exact: true });
    assert.equal(await button.getAttribute("aria-pressed"), "false");
    await button.click();
    assert.equal(await button.getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("html").getAttribute("data-theme"), "light");
    await page.locator("#volume-chart svg").waitFor({ timeout: 30000 });
    assert.equal(await page.locator("#volume-chart rect").first().evaluate(el => getComputedStyle(el).fill), "rgb(24, 121, 88)");
    await page.screenshot({ path: path.join(out, "light-desktop.png"), fullPage: true });
    await page.reload();
    assert.equal(await button.getAttribute("aria-pressed"), "true");
    const other = await context.newPage();
    await other.goto(base + "/api/docs");
    assert.equal(await other.locator("html").getAttribute("data-theme"), "light");
    await button.click();
    await other.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await page.screenshot({ path: path.join(out, "theme-dark.png"), fullPage: false });
    await button.click();
    await page.setViewportSize({ width: 390, height: 844 });
    const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: innerWidth }));
    assert(width.body <= width.viewport, JSON.stringify(width));
    await page.screenshot({ path: path.join(out, "light-mobile.png"), fullPage: true });
    await context.close();
    const auto = await browser.newContext({ colorScheme: "light" });
    const autoPage = await auto.newPage();
    await autoPage.goto(base);
    assert.equal(await autoPage.locator("html").getAttribute("data-theme"), "light");
    await autoPage.emulateMedia({ colorScheme: "dark" });
    await autoPage.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await auto.close();
    const restricted = await browser.newContext({ colorScheme: "dark" });
    await restricted.addInitScript(() => {
      Object.defineProperty(window, "localStorage", { get() { throw new DOMException("Disabled", "SecurityError"); } });
    });
    const privatePage = await restricted.newPage();
    await privatePage.goto(base);
    await privatePage.getByRole("button", { name: "Светлая тема", exact: true }).click();
    assert.equal(await privatePage.locator("html").getAttribute("data-theme"), "light");
    await restricted.close();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, errors, persistence: true, systemTheme: true, storageDisabled: true, viewport: width, screenshots: out }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
