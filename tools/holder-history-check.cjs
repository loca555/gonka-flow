/* UI QA in an isolated Chrome profile, with no changes to indexed data. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const keys=['investors','sellers','traders','unclassified'];
(async()=>{
 const base=process.env.GONKA_QA_BASE_URL||'http://127.0.0.1:8790',out=path.resolve('test-results');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const response=page.waitForResponse(r=>r.url().includes('/api/mints/flows?')&&r.status()===200);
  await page.goto(base+'/#trading');
  const data=await (await response).json(),history=data.holder_history,last=history.points.at(-1);
  assert(data.ready&&history.ready&&history.points.length>1);
  assert.equal(history.height,data.snapshot.height);
  assert.equal(last.total_raw,data.outside_holders.total_raw);
  for(const group of data.outside_holders.groups){
   assert.equal(last[group.id+'_raw'],group.balance_raw);
   assert.equal(last.addresses[group.id],group.addresses);
  }
  // Freeze this verified snapshot during UI checks so background polling cannot race a tooltip.
  await page.route('**/api/mints/flows?**',route=>route.fulfill({json:data}));
  const panel=page.locator('.holder-history-panel'),chart=page.locator('#holder-history-chart'),svg=chart.locator('svg'),tip=chart.locator('.chart-tooltip');
  await svg.waitFor();
  assert.equal(await chart.locator('.holder-history-line').count(),4);
  const cardsBox=await page.locator('#holder-group-cards').boundingBox(),panelBox=await panel.boundingBox();
  assert(panelBox.y>=cardsBox.y+cardsBox.height);
  for(const key of keys){
   const line=chart.locator('.holder-history-line[data-holder-kind="'+key+'"]');
   const d=await line.getAttribute('d');
   assert(!/NaN|Infinity/.test(d));
   assert.equal((d.match(/[ML]/g)||[]).length,history.points.length);
   assert.equal(await chart.locator('.holder-history-last[data-holder-kind="'+key+'"]').getAttribute('data-balance-raw'),last[key+'_raw']);
  }
  assert.equal(new Set(await chart.locator('.holder-history-line').evaluateAll(lines=>lines.map(line=>getComputedStyle(line).stroke))).size,4);
  async function checkTip(point){
   assert.equal(await tip.getAttribute('data-date'),point.date);
   assert.equal(await tip.getAttribute('data-total-raw'),point.total_raw);
   for(const key of keys)assert.equal(await tip.locator('[data-holder-kind="'+key+'"]').getAttribute('data-balance-raw'),point[key+'_raw']);
  }
  await svg.focus();await page.keyboard.press('Home');await checkTip(history.points[0]);
  await page.keyboard.press('End');await checkTip(last);
  await panel.screenshot({path:path.join(out,'holder-history-tooltip.png')});
  await page.keyboard.press('ArrowLeft');await checkTip(history.points.at(-2));
  await page.keyboard.press('Escape');assert(await tip.isHidden());
  await panel.screenshot({path:path.join(out,'holder-history-light.png')});
  const hit=await chart.locator('.chart-hit').boundingBox();
  await page.mouse.move(hit.x+hit.width/2,hit.y+hit.height/2);
  await checkTip(history.points[Math.round((history.points.length-1)/2)]);
  await page.mouse.move(0,0);assert(await tip.isHidden());
  await page.locator('[data-trade-side="sell"]').click();
  await svg.waitFor();
  for(const key of keys)assert.equal(await chart.locator('.holder-history-last[data-holder-kind="'+key+'"]').getAttribute('data-balance-raw'),last[key+'_raw']);
  await page.locator('#theme-toggle').click();
  await panel.screenshot({path:path.join(out,'holder-history-dark.png')});
  await page.locator('[data-view="leaders"]').click();
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-view="trading"]').click();
  await svg.waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await chart.locator('.holder-history-line').count(),4);
  assert((await svg.boundingBox()).width<=390);
  await panel.screenshot({path:path.join(out,'holder-history-mobile.png')});
  await svg.focus();await page.keyboard.press('End');await checkTip(last);
  const tipBox=await tip.boundingBox(),chartBox=await chart.boundingBox();
  assert(tipBox.x>=chartBox.x&&tipBox.x+tipBox.width<=chartBox.x+chartBox.width+1);
  await panel.screenshot({path:path.join(out,'holder-history-mobile-tooltip.png')});
  await page.keyboard.press('Escape');
  // Explicit unavailable history never turns into four zero balances.
  await page.unroute('**/api/mints/flows?**');
  await page.route('**/api/mints/flows?**',route=>route.fulfill({json:{...data,holder_history:{...history,ready:false,points:[]}}}));
  await page.locator('#refresh').click();
  await chart.locator('.empty').waitFor();
  assert.match(await chart.innerText(),/не нулевые балансы/);
  assert.equal(await chart.locator('svg').count(),0);
  await page.unroute('**/api/mints/flows?**');
  await page.route('**/api/mints/flows?**',route=>route.fulfill({json:data}));
  await page.locator('#refresh').click();await svg.waitFor();
  // Network failure preserves the previous verified series.
  const saved=await chart.locator('.holder-history-line').evaluateAll(lines=>lines.map(line=>line.getAttribute('d')));
  await page.unroute('**/api/mints/flows?**');
  await page.route('**/api/mints/flows?**',route=>route.fulfill({status:503,body:'local test outage'}));
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.getElementById('trade-status').textContent.includes('Нет свежего ответа'));
  assert.deepEqual(await chart.locator('.holder-history-line').evaluateAll(lines=>lines.map(line=>line.getAttribute('d'))),saved);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,days:history.points.length,first:history.points[0].date,last:last.date,height:history.height,exactTotals:true,keyboard:true,hover:true,lightDark:true,mobile:true,outage:true,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
