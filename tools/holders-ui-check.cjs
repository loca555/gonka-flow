/* Separate clean browser; no wallets, no mutations outside the local test UI. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
 const base=process.env.TEST_URL||'http://127.0.0.1:8797',out='test-results';await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100},colorScheme:'light'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const api=async path=>{const r=await page.request.get(base+path);assert(r.ok());return r.json();};
  await page.goto(base+'/#holders');
  const idle=asset=>page.waitForFunction(a=>document.querySelector('#holders-'+a)?.getAttribute('aria-busy')==='false',asset);
  for(const asset of ['WGNK','GNK']){
   await idle(asset);const panel=page.locator('#holders-'+asset),data=await api('/api/mints/holders?asset='+asset);
   assert(data.ready);assert.equal(data.minimum,10000);assert(data.total>25);
   const rows=await panel.locator('tbody tr[data-address]').evaluateAll(nodes=>nodes.map(n=>n.dataset));
   assert.equal(rows.length,25);assert.deepEqual(rows.map(r=>r.address),data.items.map(r=>r.address));
   assert(rows.every(r=>BigInt(r.balanceRaw)>=10000n*10n**9n));
   await panel.locator('[data-next]').click();await idle(asset);
   assert.equal(await panel.locator('tbody tr[data-address]').first().locator('td').first().innerText(),'26');
   await panel.locator('[data-prev]').click();await idle(asset);
   await panel.locator('input').fill(data.items[2].address);await panel.locator('button[type=submit]').click();await idle(asset);
   assert.equal(await panel.locator('tbody tr[data-address]').count(),1);
   assert.equal(await panel.locator('tbody tr[data-address]').getAttribute('data-address'),data.items[2].address);
   await panel.locator('input').fill('zzzzzzz');await panel.locator('button[type=submit]').click();await idle(asset);
   assert.match(await panel.locator('tbody').innerText(),/Адрес не найден/);assert.equal(await panel.locator('tbody tr[data-address]').count(),0);
   await panel.locator('[data-reset]').click();await idle(asset);
  }
  assert.equal(await page.locator('[data-view=holders]').getAttribute('aria-current'),'page');
  assert(await page.locator('#leaders-view').isHidden());
  await page.screenshot({path:out+'/holders-desktop.png',fullPage:true});
  await page.locator('[data-view=leaders]').click();
  const address='0x0a34924dd04ac5e5589c5e849c95d927bfd50bd1';
  const opener=page.locator('#leaders-buy-table [data-flow-address="'+address+'"]');await opener.waitFor();
  const [resp]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/mints/address/'+address+'?')),opener.click()]);
  const data=await resp.json(),points=data.address_history.points;
  const svg=page.locator('#address-chart svg');await svg.waitFor();
  await page.waitForFunction(()=>document.querySelector('#address-dialog').getAttribute('aria-busy')==='false');
  const pos=points.findIndex(p=>BigInt(p.bought_raw)>0n);assert(pos>=0);
  await svg.press('Home');for(let i=0;i<pos;i++)await svg.press('ArrowRight');
  const tip=page.locator('#address-chart .chart-tooltip');
  assert.equal(await tip.getAttribute('data-date'),points[pos].date);
  for(const [side,volume,quote] of [['buy','bought_raw','buy_quote_raw'],['sell','sold_raw','sale_quote_raw']]){
   const raw=BigInt(points[pos][volume])>0n?BigInt(points[pos][quote])*10n**15n/BigInt(points[pos][volume]):null;
   const expected=await page.evaluate(r=>GonkaChart.formatPrice(r),raw===null?null:String(raw));
   assert.equal(await tip.locator('[data-trade-kind='+side+'] .address-day-price b').innerText(),expected);
  }
  await page.locator('.address-chart-panel').screenshot({path:out+'/address-average-price.png'});
  await page.locator('#address-close').click();await page.locator('[data-view=holders]').click();
  await page.route('**/api/mints/holders?*',r=>r.fulfill({status:503,body:'test outage'}));
  await page.locator('#holders-refresh').click();await idle('WGNK');
  assert.match(await page.locator('#holders-WGNK .holders-status').innerText(),/предыдущий снимок/);
  assert.equal(await page.locator('#holders-WGNK tbody tr[data-address]').count(),25);
  await page.locator('#holders-WGNK input').fill('abc');await page.locator('#holders-WGNK button[type=submit]').click();await idle('WGNK');
  assert.equal(await page.locator('#holders-WGNK tbody tr[data-address]').count(),0);
  await page.unroute('**/api/mints/holders?*');await page.locator('#holders-WGNK [data-reset]').click();await idle('WGNK');
  await page.locator('#theme-toggle').click();await page.screenshot({path:out+'/holders-dark.png',fullPage:true});
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert(await page.locator('#holders-GNK').isVisible());
   await page.screenshot({path:out+'/holders-mobile-'+width+'.png',fullPage:true});
  }
  const holderButton=page.locator('#holders-WGNK [data-holder-address]').nth(1);
  const holderAddress=await holderButton.getAttribute('data-holder-address');await holderButton.click();
  await page.locator('#address-chart svg').waitFor();
  assert.match(await page.locator('#address-link').innerText(),new RegExp(holderAddress));
  await page.locator('#address-chart svg').press('End');
  const mobileTip=page.locator('#address-chart .chart-tooltip');
  assert.equal(await mobileTip.locator('.address-day-price').count(),2);
  const box=await mobileTip.boundingBox();assert(box.x>=0&&box.x+box.width<=320);
  await page.screenshot({path:out+'/address-average-mobile.png'});
  await page.locator('#address-close').click();
  await page.reload();await idle('WGNK');await idle('GNK');assert(await page.locator('#holders-view').isVisible());
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,holders:true,threshold:10000,search:true,pagination:true,averagePrices:true,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
