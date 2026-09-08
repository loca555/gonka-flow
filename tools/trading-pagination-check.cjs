/* Read-only pagination regression in a fresh Chrome/Brave profile. No wallets or user data. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const base=process.env.PAGINATION_TEST_BASE||'http://127.0.0.1:8790';
(async()=>{
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,
  ...(process.env.TEST_BROWSER_PATH?{executablePath:process.env.TEST_BROWSER_PATH}:{channel:'chrome'})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const get=async url=>{const response=await context.request.get(base+url);assert.equal(response.status(),200,url);return response.json();};
  const fixture=await get('/api/mints/flows?side=all&limit=60');
  assert(fixture.ready&&fixture.trades.length===60,'fixture needs at least 60 verified trades');
  for(const offset of [0,25,50]){
   const data=await get('/api/mints/flows?side=all&limit=25&offset='+offset);
   assert.equal(data.offset,offset);assert.equal(data.limit,25);assert.equal(data.trades.length,25);
  }
  const page=await context.newPage(),errors=[],offsets=[];
  let mode='normal',release,held,heldDone;
  page.on('pageerror',error=>errors.push(error.message));
  if(process.env.PAGINATION_SOURCE){
   await page.route(base+'/static/flows.js*',route=>route.fulfill({path:process.env.PAGINATION_SOURCE,contentType:'application/javascript'}));
  }
  function hold(){
   mode='hold';
   held=new Promise(resolve=>release={ready:resolve});
   heldDone=new Promise(resolve=>release.done=resolve);
  }
  await page.route(base+'/api/mints/flows?*',async route=>{
   assert.equal(route.request().method(),'GET');
   const url=new URL(route.request().url()),offset=Number(url.searchParams.get('offset')),limit=Number(url.searchParams.get('limit'));
   offsets.push(offset);
   const behavior=mode;mode='normal';
   const data={...structuredClone(fixture),total:60,offset,limit,
    trades:fixture.trades.slice(offset,offset+limit),sales:[],has_more:offset+limit<60};
   if(behavior==='fail')return route.fulfill({status:503,json:{detail:'Test temporary failure'}});
   if(behavior==='recovering')data.ready=false;
   let pending;
   if(behavior==='hold'){
    pending=release;
    await new Promise(resolve=>{pending.finish=resolve;pending.ready();});
   }
   try{await route.fulfill({json:data});}catch(error){
    if(behavior!=='hold')throw error;
   }finally{pending?.done();}
  });
  await page.goto(base+'/#trading');
  const idle=()=>page.waitForFunction(()=>document.getElementById('sales-search').getAttribute('aria-busy')==='false');
  const range=()=>page.locator('#sales-page').textContent();
  const first=()=>page.locator('#sales-rows .tx-link').first().getAttribute('href');
  async function checkPage(offset){
   await idle();
   assert.equal(await range(),(offset+1)+'–'+Math.min(offset+25,60)+' из 60');
   assert.equal(await page.locator('#sales-rows tr').count(),Math.min(25,60-offset));
   assert.equal(await first(),'https://etherscan.io/tx/'+fixture.trades[offset].tx_hash);
   assert.equal(await page.locator('#sales-prev').isEnabled(),offset>0);
   assert.equal(await page.locator('#sales-next').isEnabled(),offset+25<60);
  }
  async function click(id,offset){
   await page.locator('#'+id).click();await idle();
   assert.equal(offsets.at(-1),offset,'navigation must request the adjacent displayed page');
   await checkPage(offset);
  }
  await page.locator('#flow-content').waitFor({state:'visible'});
  await checkPage(0);
  await click('sales-next',25);
  // A failed next request must not advance the cursor; retry still requests 51-75.
  const previous=await first();mode='fail';
  await page.locator('#sales-next').click();await idle();
  assert.equal(await range(),'26–50 из 60');assert.equal(await first(),previous);
  await click('sales-next',50);
  await click('sales-prev',25);
  // While a response is pending, show a local status and ignore repeated clicks.
  hold();await page.locator('#sales-next').click();await held;
  assert.equal(await page.locator('#sales-prev').isEnabled(),false);
  assert.equal(await page.locator('#sales-next').isEnabled(),false);
  assert.equal(await range(),'26–50 из 60');
  assert.match(await page.locator('#sales-page-status').innerText(),/Загружаем/);
  const count=offsets.length;
  await page.locator('#sales-next').evaluate(button=>button.click());
  assert.equal(offsets.length,count,'repeated click must not skip a page or restart the request');
  await fs.mkdir('test-results',{recursive:true});
  await page.locator('#sales-pagination').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join('test-results','trading-pagination-loading.png')});
  release.finish();await heldDone;await checkPage(50);
  await click('sales-prev',25);
  // Recovery responses retain the visible page and expose a retry message beside the controls.
  mode='recovering';await page.locator('#sales-next').click();await idle();
  await checkPage(25);
  assert.equal(await page.locator('#flow-content').isVisible(),true);
  assert.match(await page.locator('#sales-page-status').innerText(),/недоступна/);
  await click('sales-next',50);
  await click('sales-prev',25);
  // Background refresh must not block navigation or overwrite a newer page.
  hold();await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await held;
  assert.equal(await page.locator('#sales-next').isEnabled(),true);
  await click('sales-next',50);release.finish();await heldDone;await checkPage(50);
  await click('sales-prev',25);
  // Reset filters while a page is loading: a late response cannot undo the reset.
  hold();await page.locator('#sales-next').click();await held;
  await page.locator('#sales-reset').click();await checkPage(0);
  release.finish();await heldDone;await checkPage(0);
  await page.setViewportSize({width:390,height:844});
  await click('sales-next',25);await click('sales-next',50);await click('sales-prev',25);
  await page.locator('#sales-pagination').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join('test-results','trading-pagination-mobile.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,browser:process.env.TEST_BROWSER_PATH?'Brave':'Chrome',
   checks:['public-api-pages','next-next-back','retry-same-page-after-503','pending-click-guard',
    'local-loading-status','first-last-page-boundaries','snapshot-recovery','background-refresh-race',
    'reset-during-navigation','mobile-pagination'],requests:offsets.length}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
