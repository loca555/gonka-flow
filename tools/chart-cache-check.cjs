/* Reproduces a warm browser cache using the previous chart.js, without touching user profiles. */
const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs/promises');
(async()=>{
 const base='http://127.0.0.1:8790',address='0x0a34924dd04ac5e5589c5e849c95d927bfd50bd1';
 const previous=execFileSync('git',['show','cf00a6f:app/static/chart.js'],{encoding:'utf8'});
 assert(!previous.includes('function renderAddress'));
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  let oldRequests=0;
  await page.route('**/static/chart.js',r=>{oldRequests++;return r.fulfill({contentType:'text/javascript',body:previous});});
  await page.goto(base+'/#leaders');
  const open=async()=>{
   await page.locator('#leaders-buy-table [data-flow-address="'+address+'"]').click();
   await page.waitForFunction(()=>document.getElementById('address-dialog').getAttribute('aria-busy')==='false');
  };
  await open();
  if(process.argv.includes('--reproduce')){
   assert.equal(await page.locator('#address-chart svg').count(),0);
   assert.match(await page.locator('#address-status').innerText(),/renderAddress/);
   console.log(JSON.stringify({reproduced:true,oldRequests,status:await page.locator('#address-status').innerText()}));
   return;
  }
  assert.equal(oldRequests,0,'New HTML must not request the stale unversioned script');
  await page.locator('#address-chart svg').waitFor();
  assert.deepEqual(errors,[]);
  const scripts=await page.locator('script[src]').evaluateAll(nodes=>nodes.map(n=>n.src));
  assert(scripts.every(src=>/\?v=[a-f0-9]{16}$/.test(src)));
  const chartResponse=await context.request.get(scripts.find(src=>src.includes('/chart.js?')));
  assert.equal(chartResponse.headers()['cache-control'],'no-cache');
  // A component mismatch should show a recovery action, not hide trades or throw globally.
  await page.locator('#address-close').click();
  await page.route('**/static/chart.js?*',r=>r.fulfill({contentType:'text/javascript',body:previous}));
  await page.reload();await open();
  assert.equal(await page.locator('#address-chart svg').count(),0);
  assert.match(await page.locator('#address-chart').innerText(),/Обновить страницу/);
  assert(await page.locator('#address-data').isVisible());
  assert(await page.locator('#address-rows tr').count()>0);
  assert.doesNotMatch(await page.locator('#address-status').innerText(),/Не удалось обновить сделки/);
  const out=path.resolve('test-results');await fs.mkdir(out,{recursive:true});
  await page.locator('.address-chart-panel').screenshot({path:path.join(out,'address-chart-recovery.png')});
  await page.unroute('**/static/chart.js?*');
  await Promise.all([page.waitForEvent('load'),page.locator('#address-chart button').click()]);
  await open();await page.locator('#address-chart svg').waitFor();
  await page.locator('.address-chart-panel').screenshot({path:path.join(out,'address-chart-cache-fixed.png')});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,oldCacheBypassed:true,recoveryButton:true,tradesPreserved:true,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
