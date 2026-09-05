/* Read-only localhost QA in a separate headless Chrome. Never attaches to a user profile. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const base = process.env.TEST_URL || "http://127.0.0.1:8790";
  const out = path.resolve("test-results");
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1040 }, colorScheme: "dark" });
    const page = await context.newPage(), errors = [], apiRequests = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("request", r => { if (new URL(r.url()).pathname.startsWith("/api/")) apiRequests.push(new URL(r.url()).pathname); });
    const get = async route => {
      const r = await context.request.get(base + route);
      assert.equal(r.status(), 200, route + " " + await r.text());
      return r.json();
    };
    const baseline = await get("/api/mints?minimum=0&limit=200");
    const initial = await get("/api/mints?minimum=10000&limit=50");
    assert.equal(baseline.mode, "mints");
    assert.equal(baseline.coverage.complete, true, "Full history is required for this live-data QA");
    assert.equal(baseline.coverage.missing, 0);
    assert.deepEqual([...baseline.workers].sort(), ["wgnk_market_flow", "wgnk_mints_history", "wgnk_mints_live"]);
    assert.equal(baseline.timezone, "Asia/Nicosia");
    assert(baseline.all_summary.events > 0);
    assert(baseline.coverage.deployment.ts <= baseline.first_mint);
    async function rendered(data) {
      await page.waitForFunction(n => document.getElementById("row-count").textContent.replace(/\s/g, "") === String(n), data.total);
      if (data.items.length) {
        const first = data.items[0];
        await page.waitForFunction(e => {
          const b = document.querySelector("#mint-rows [data-tx]");
          return b && b.dataset.tx === e.tx_hash && Number(b.dataset.log) === e.log_index;
        }, first);
      }
      assert.equal(await page.locator("#error-banner").isVisible(), false);
    }
    async function act(action, expected = () => true) {
      const responsePromise = page.waitForResponse(r => {
        const u = new URL(r.url());
        return u.pathname === "/api/mints" && expected(u.searchParams) && r.status() === 200;
      });
      const [response] = await Promise.all([responsePromise, action()]);
      const data = await response.json();
      await rendered(data);
      return data;
    }

    await page.goto(base + "/#holders");
    await rendered(initial);
    assert.equal(new URL(page.url()).hash, "#mints");
    assert.equal(await page.locator("#minimum").inputValue(), "10000");
    assert.equal(await page.locator("#period").inputValue(), "0");
    assert.equal(await page.evaluate(() => amount("0.000000001")), "< 0.0001");
    assert.equal(await page.evaluate(() => amount("0.000000001", 9)), "0.000000001");
    assert.equal(await page.evaluate(() => amount("1234567.890001",9).replace(/\s/g," ")), "1 234 567.890001");
    assert.equal(await page.evaluate(() => count(40.17)), "40.17");
    assert.equal(await page.evaluate(() => GonkaChart.exact("1234567890001",6).replace(/\s/g," ")), "1 234 567.890001");
    assert.match(await page.locator("#deployment-block").innerText(), new RegExp(String(baseline.coverage.deployment.height).split("").join("\\s*")));
    assert(initial.items.every(e => BigInt(e.amount_raw) >= 10000n * 1000000000n));
    await page.locator("#mint-chart svg").waitFor();
    await page.screenshot({ path: path.join(out, "mints-dark.png"), fullPage: false });
    await page.getByRole("button", { name: "Светлая тема", exact: true }).click();
    assert.equal(await page.locator("html").getAttribute("data-theme"), "light");
    await page.screenshot({ path: path.join(out, "mints-light.png"), fullPage: false });
    await page.reload();
    await rendered(initial);
    assert.equal(await page.locator("html").getAttribute("data-theme"), "light");

    const all = await act(() => page.getByRole("button", { name: "Все выпуски", exact: true }).click(), q => q.get("minimum") === "0");
    assert.equal(all.total, baseline.total);
    assert.equal(await page.locator("#minimum").inputValue(), "0");
    const next = await act(() => page.locator("#next").click(), q => q.get("offset") === "50");
    assert.equal(next.offset, 50);
    assert.notEqual(next.items[0].tx_hash, all.items[0].tx_hash);
    await act(() => page.locator("#prev").click(), q => q.get("offset") === "0");
    const largest = await act(() => page.locator("#sort").selectOption("largest"), q => q.get("sort") === "largest");
    assert.equal(largest.items[0].amount, baseline.all_summary.largest);
    assert(largest.items.every((e, i) => !i || BigInt(largest.items[i-1].amount_raw) >= BigInt(e.amount_raw)));

    const chosen = largest.items[0];
    await page.locator('#mint-rows [data-tx="' + chosen.tx_hash + '"][data-log="' + chosen.log_index + '"]').click();
    await page.getByRole("dialog").waitFor();
    await page.locator("#mint-detail .detail-amount").waitFor();
    const detailText = await page.locator("#mint-detail").innerText();
    assert(detailText.includes(chosen.request_id));
    assert(detailText.includes(chosen.amount_raw));
    assert(detailText.includes("Сторона GNK не проверялась"));
    assert.equal(await page.locator("#mint-detail a").getAttribute("href"), "https://etherscan.io/tx/" + chosen.tx_hash);
    await page.screenshot({ path: path.join(out, "mints-detail.png"), fullPage: false });
    const recipient = await act(() => page.locator("#mint-detail [data-recipient]").click(), q => q.get("q") === chosen.recipient);
    assert.equal(await page.getByRole("dialog").isVisible(), false);
    assert(recipient.items.every(e => e.recipient === chosen.recipient));
    assert.equal(await page.locator("#minimum").inputValue(), "0");

    await act(() => page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click(), q => q.get("q") === "" && q.get("minimum") === "10000");
    await act(() => page.getByRole("button", { name: "Все выпуски", exact: true }).click(), q => q.get("minimum") === "0");
    await page.getByRole("textbox", { name: "Поиск выпуска", exact: true }).fill(chosen.tx_hash);
    const search = await act(() => page.getByRole("button", { name: "Найти ↗", exact: true }).click(), q => q.get("q") === chosen.tx_hash);
    assert(search.items.every(e => e.tx_hash === chosen.tx_hash));
    const filteredCsvResponse = await context.request.get(base + await page.locator("#csv-filtered").getAttribute("href"));
    assert.equal(filteredCsvResponse.status(), 200);
    const filteredCsv = await filteredCsvResponse.text();
    assert.equal(filteredCsv.trim().split(/\r?\n/).length - 1, search.total);
    assert(filteredCsv.includes(chosen.amount_raw));
    const csvResponse = await context.request.get(base + await page.locator("#csv-all").getAttribute("href"));
    assert.equal(csvResponse.status(), 200);
    const csv = await csvResponse.text();
    assert.equal(csv.trim().split(/\r?\n/).length - 1, baseline.total);

    await act(() => page.locator("#pending").check(), q => q.get("finality") === "all");
    await act(() => page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click(), q => q.get("finality") === "finalized" && q.get("q") === "");
    await act(() => page.locator("#period").selectOption("24"), q => q.get("hours") === "24");
    await act(() => page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click(), q => q.get("hours") === "0");
    const threshold = await act(async () => {
      await page.getByRole("spinbutton", { name: "Минимальный выпуск", exact: true }).fill("200000");
      await page.getByRole("spinbutton", { name: "Минимальный выпуск", exact: true }).press("Tab");
    }, q => q.get("minimum") === "200000");
    assert(threshold.items.every(e => BigInt(e.amount_raw) >= 200000n * 1000000000n));
    await act(() => page.getByRole("button", { name: "Сбросить фильтры", exact: true }).click(), q => q.get("minimum") === "10000");
    await page.getByRole("textbox", { name: "Поиск выпуска", exact: true }).fill("not-an-address");
    await page.getByRole("button", { name: "Найти ↗", exact: true }).click();
    assert.match(await page.locator("#toast").innerText(), /0x/);
    await page.getByRole("textbox", { name: "Поиск выпуска", exact: true }).fill("");

    const priorAmount = await page.locator("#metric-amount").innerText();
    await page.route("**/api/mints?*", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"test outage"}' }));
    await page.locator("#refresh").click();
    await page.locator("#error-banner").waitFor({ state: "visible" });
    assert.equal(await page.locator("#metric-amount").innerText(), priorAmount);
    assert.equal(await page.locator("#live-caption").innerText(), "Нет свежего ответа сервера");
    await page.unroute("**/api/mints?*");
    await act(() => page.locator("#refresh").click());
    const draftAddress = chosen.recipient.slice(0, 15);
    await page.getByRole("textbox", { name: "Поиск выпуска", exact: true }).fill(draftAddress);
    await page.waitForResponse(r => new URL(r.url()).pathname === "/api/mints" && r.status() === 200);
    assert.equal(await page.locator("#query").inputValue(), draftAddress, "Polling must preserve an unsubmitted search");
    await page.locator("#query").fill("");
    await page.locator("#toast").waitFor({ state: "hidden" });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    const width = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: innerWidth }));
    assert(width.body <= width.viewport, "Mobile overflow: " + JSON.stringify(width));
    await page.waitForFunction(() => document.querySelector("#mint-chart svg").viewBox.baseVal.width <= 354);
    await page.screenshot({ path: path.join(out, "mints-mobile.png"), fullPage: false });
    await page.locator(".insights").screenshot({ path: path.join(out, "mints-mobile-insights.png") });
    await page.locator(".transactions").screenshot({ path: path.join(out, "mints-mobile-table.png") });
    await page.getByRole("button", { name: "Светлая тема", exact: true }).click();
    assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");

    for (const route of ["/api/overview", "/api/holders", "/api/hosts", "/api/events", "/api/stream", "/api/addresses/0x0000000000000000000000000000000000000001"]) {
      assert.equal((await context.request.get(base + route)).status(), 410, route);
    }
    assert(apiRequests.every(route => route === "/api/mints" || route.startsWith("/api/mints/")), JSON.stringify(apiRequests));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, events: baseline.total, defaultLarge: initial.total,
      deployment: baseline.coverage.deployment, complete: baseline.coverage.complete, viewport: width,
      checks: ["full history","default 10k","all mints","pagination","largest sort","receipt details","recipient filter","tx search","all CSV","filtered CSV","period","custom threshold","provisional toggle","tiny exact amounts","API outage","polling preserves search","theme persistence","mobile","disabled APIs","mint-only requests"],
      screenshots: out, errors }));
    await context.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
