/* Read-only, isolated browser QA. Real API data; mocked failures never touch the DB. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.TEST_URL||'http://127.0.0.1:8790',address='0x0a34924dd04ac5e5589c5e849c95d927bfd50bd1';
 const out=path.resolve('test-results');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#leaders');
  const opener=page.locator('#leaders-buy-table [data-flow-address="'+address+'"]');
  await opener.waitFor();
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/mints/address/'+address+'?')),opener.click()]);
  const data=await response.json();assert(data.ready);assert(data.address_history);
  const points=data.address_history.points,chart=page.locator('#address-chart'),svg=chart.locator('svg');
  const ready=()=>page.waitForFunction(()=>document.getElementById('address-dialog').getAttribute('aria-busy')==='false');
  await ready();await svg.waitFor();
  assert.equal(points.at(-1).balance_raw,data.address_balance.amount_raw);
  for(const key of ['bought_raw','sold_raw','buys_count','sales_count']){
   assert.equal(points.reduce((s,p)=>s+BigInt(p[key]),0n),BigInt(data.summary[key]));
  }
  for(const [side,key] of [['buy','bought_raw'],['sell','sold_raw']]){
   const bars=await chart.locator('.address-volume.'+side).evaluateAll(nodes=>nodes.map(n=>n.dataset.raw));
   assert.equal(bars.reduce((s,n)=>s+BigInt(n),0n),BigInt(data.summary[key]));
  }
  assert.equal(await chart.locator('.address-balance-last').getAttribute('data-balance-raw'),data.address_balance.amount_raw);
  assert(!/NaN|Infinity/.test(await svg.innerHTML()));
  await svg.press('End');
  const tip=chart.locator('.chart-tooltip');
  assert.equal(await tip.getAttribute('data-balance-raw'),data.address_balance.amount_raw);
  assert.match(await tip.innerText(),/Баланс на снимке/);
  await svg.press('Home');assert.equal(await tip.getAttribute('data-date'),points[0].date);
  await svg.press('ArrowRight');assert.equal(await tip.getAttribute('data-date'),points[1].date);
  await svg.hover({position:{x:200,y:100}});assert(await tip.isVisible());
  await page.mouse.move(20,20);
  await page.locator('.address-chart-panel').screenshot({path:path.join(out,'address-chart-light.png')});
  await page.locator('#address-dialog').evaluate(d=>d.scrollTop=0);
  await page.screenshot({path:path.join(out,'address-chart-modal.png')});
  const lineBefore=await chart.locator('.address-balance-line').getAttribute('d');
  await page.locator('#address-trades-table th[data-sort="amount"] button').click();await ready();
  assert.equal(await chart.locator('.address-balance-line').getAttribute('d'),lineBefore);
  await page.locator('#address-tab-gonka').click();assert.equal(await chart.isVisible(),false);
  await page.locator('#address-tab-trades').click();assert(await svg.isVisible());
  const table=page.locator('.address-table');await table.hover();await page.mouse.wheel(0,300);
  await page.waitForFunction(()=>document.querySelector('.address-table').scrollTop>0);
  assert(await page.locator('#address-dialog').evaluate(d=>d.open));
  const reload=async()=>{await page.locator('#address-refresh').click();await ready();};
  await page.route('**/api/mints/address/'+address+'?*',r=>r.fulfill({status:503,body:'test outage'}));
  await reload();assert.match(await page.locator('#address-status').innerText(),/предыдущий результат/);
  assert.equal(await chart.locator('.address-balance-last').getAttribute('data-balance-raw'),data.address_balance.amount_raw);
  await page.unroute('**/api/mints/address/'+address+'?*');await reload();
  await page.locator('#address-close').click();await page.locator('#theme-toggle').click();
  await opener.click();await ready();await svg.waitFor();
  await page.locator('.address-chart-panel').screenshot({path:path.join(out,'address-chart-dark.png')});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>{
   const c=document.getElementById('address-chart'),s=c.querySelector('svg');
   return s&&Math.abs(s.viewBox.baseVal.width-Math.max(280,c.clientWidth))<1;
  });
  await page.locator('.address-chart-panel').screenshot({path:path.join(out,'address-chart-mobile.png')});
  assert(await page.locator('#address-dialog').evaluate(d=>d.scrollWidth<=d.clientWidth+2));
  const box=await svg.boundingBox();await page.mouse.click(box.x+box.width*.7,box.y+100);
  assert(await tip.isVisible());
  const tipBox=await tip.boundingBox();assert(tipBox.x>=0&&tipBox.x+tipBox.width<=391);
  // Stale-address data must not leak into empty or unverified responses.
  await page.route('**/api/mints/address/'+address+'?*',r=>r.fulfill({json:{...data,address_history:null}}));
  await reload();assert.equal(await svg.count(),0);assert.match(await chart.innerText(),/недоступна/);
  await page.unroute('**/api/mints/address/'+address+'?*');
  await page.route('**/api/mints/address/'+address+'?*',r=>r.fulfill({json:{...data,address_history:{...data.address_history,points:[],balance_raw:'0'}}}));
  await reload();assert.equal(await svg.count(),0);assert.match(await chart.innerText(),/нет движений/);
  await page.unroute('**/api/mints/address/'+address+'?*');await reload();await svg.waitFor();
  await page.locator('#address-close').click();await page.setViewportSize({width:1440,height:1100});
  const other=page.locator('#leaders-sell-rows [data-flow-address]').first();
  const otherAddress=await other.getAttribute('data-flow-address');assert.notEqual(otherAddress,address);
  await page.route('**/api/mints/address/'+otherAddress+'?*',r=>r.fulfill({status:503,body:'test outage'}));
  await other.click();await ready();assert.equal(await chart.locator('svg').count(),0);
  assert.equal(await page.locator('#address-data').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,days:points.length,buys:data.summary.buys_count,sells:data.summary.sales_count,balance:data.address_balance.amount,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
