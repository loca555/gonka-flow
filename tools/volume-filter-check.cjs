/* Read-only checks against a local snapshot in a fresh Chromium profile. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.TEST_PLAYWRIGHT_MODULE||'playwright');
const base=process.env.VOLUME_TEST_BASE||'http://127.0.0.1:8796';
(async()=>{
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,
  ...(process.env.TEST_BROWSER_PATH?{executablePath:process.env.TEST_BROWSER_PATH}:{channel:'chrome'})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200,url);return r.json();};
  const original=await get('/api/mints/flows?side=all&minimum=0');
  const originalBridge=await get('/api/mints/bridge?minimum=0');
  assert(original.ready&&original.total>25&&originalBridge.ready&&originalBridge.total>50);
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const responseFor=(endpoint,params)=>page.waitForResponse(response=>{
   const url=new URL(response.url());
   return url.origin===new URL(base).origin&&url.pathname===endpoint&&
    Object.entries(params).every(([key,value])=>url.searchParams.get(key)===String(value));
  });
  const action=async(endpoint,params,run)=>{
   const pending=responseFor(endpoint,params);await run();
   const response=await pending;assert.equal(response.status(),200);return response.json();
  };
  const rendered=async(prefix,total)=>{
   const id=prefix==='sales'?'sales-total':'row-count';
   await page.waitForFunction(({id,total})=>Number(document.getElementById(id).textContent.replace(/\s/g,''))===total,{id,total});
   if(prefix==='sales')await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  };
  const apply=async(prefix,value)=>{
   await page.locator('#'+prefix+'-minimum').fill(String(value));
   const endpoint=prefix==='sales'?'/api/mints/flows':'/api/mints/bridge';
   const data=await action(endpoint,{minimum:value,offset:0},()=>page.locator('#'+prefix+'-volume-filter button[type="submit"]').click());
   await rendered(prefix,data.total);
   assert.equal(data.minimum,Number(value));
   assert((data.trades||data.items).every(row=>BigInt(row.amount_raw)>=BigInt(value)*1000000000n));
   return data;
  };
  await page.goto(base+'/#trading');
  await rendered('sales',original.total);
  assert.equal(await page.locator('#sales-minimum').inputValue(),'0');
  await action('/api/mints/trades',{minimum:0,offset:25},()=>page.locator('#sales-next').click());
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  assert((await page.locator('#sales-page').innerText()).startsWith('26–'));
  const filtered=await apply('sales',1000);
  assert(filtered.total>25&&filtered.total<original.total);
  assert((await page.locator('#sales-page').innerText()).startsWith('1–25'));
  const second=await action('/api/mints/trades',{minimum:1000,offset:25},()=>page.locator('#sales-next').click());
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  assert.equal(second.total,filtered.total);
  assert(second.trades.every(row=>BigInt(row.amount_raw)>=1000000000000n));
  await page.locator('#sales-prev').click();
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  assert((await page.locator('#sales-page').innerText()).startsWith('1–25'));
  await action('/api/mints/flows',{minimum:1000,sort:'amount_desc'},()=>page.locator('#trades-table th[data-sort="amount"] button').click());
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  await action('/api/mints/flows',{minimum:1000,side:'buy'},()=>page.locator('[data-trade-side="buy"]').click());
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();
  assert((await page.locator('#sales-rows .trade-badge').allTextContents()).every(text=>text==='Покупка'));
  await apply('sales',1000000000000);
  assert.match(await page.locator('#sales-rows').innerText(),/не найдено/);
  await action('/api/mints/flows',{minimum:0,side:'all'},()=>page.locator('#sales-reset').click());
  await rendered('sales',original.total);
  assert.equal(await page.locator('#sales-minimum').inputValue(),'0');
  assert(await page.locator('#sales-volume-filter [data-volume-reset]').isDisabled());
  await apply('sales',1000);
  await page.locator('a[data-view="bridge"]').click();
  await rendered('bridge',originalBridge.total);
  assert.equal(await page.locator('#bridge-minimum').inputValue(),'0');
  const bridge=await apply('bridge',10000);
  assert(bridge.total>50&&bridge.total<originalBridge.total);
  const csvUrl=await page.locator('#csv-filtered').getAttribute('href');
  assert.equal(new URL(csvUrl,base).searchParams.get('minimum'),'10000');
  const csv=await context.request.get(base+csvUrl);assert.equal(csv.status(),200);
  const lines=(await csv.text()).trim().split(/\r?\n/),headers=lines.shift().split(',');
  assert.equal(lines.length,bridge.total);
  assert(lines.every(line=>BigInt(line.split(',')[headers.indexOf('amount_raw')])>=10000000000000n));
  await action('/api/mints/bridge',{minimum:10000,offset:50},()=>page.locator('#next').click());
  await page.waitForFunction(()=>document.getElementById('page-info').textContent.startsWith('51–'));
  await action('/api/mints/bridge',{minimum:10000,offset:0,sort:'amount_desc'},()=>page.locator('#mint-table th[data-sort="amount"] button').click());
  await page.waitForFunction(()=>document.getElementById('page-info').textContent.startsWith('1–'));
  const refreshBridge=responseFor('/api/mints/bridge',{minimum:10000});
  const refreshTrades=responseFor('/api/mints/flows',{minimum:1000});
  await Promise.all([refreshBridge,refreshTrades]);
  assert.equal(await page.locator('#bridge-minimum').inputValue(),'10000');
  assert.equal(await page.locator('#sales-minimum').inputValue(),'1000');
  await fs.mkdir(path.resolve('test-results'),{recursive:true});
  await page.locator('#bridge-volume-filter').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('test-results/volume-bridge-desktop.jpg'),type:'jpeg',quality:65});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#bridge-volume-filter').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('test-results/volume-bridge-mobile.jpg'),type:'jpeg',quality:65});
  assert(await page.locator('#bridge-volume-filter').evaluate(form=>[form,...form.children,...form.querySelectorAll('input,button')].every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;})),'mobile bridge filter overflow');
  await apply('bridge',1000000000000);
  assert.match(await page.locator('#mint-rows').innerText(),/Уменьшите порог/);
  await action('/api/mints/bridge',{minimum:0,offset:0},()=>page.locator('#bridge-volume-filter [data-volume-reset]').click());
  await rendered('bridge',originalBridge.total);
  assert.equal(await page.locator('#sales-minimum').inputValue(),'1000');
  await page.goto(base+'/#trading');
  await rendered('sales',filtered.total);
  assert.equal(await page.locator('#sales-minimum').inputValue(),'1000');
  await page.locator('#sales-volume-filter').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('test-results/volume-trades-mobile.jpg'),type:'jpeg',quality:65});
  assert(await page.locator('#sales-volume-filter').evaluate(form=>[form,...form.children,...form.querySelectorAll('input,button')].every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;})),'mobile trading filter overflow');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#sales-volume-filter').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve('test-results/volume-trades-desktop.jpg'),type:'jpeg',quality:65});
  await page.locator('#sales-minimum').fill('-1');
  await page.locator('#sales-volume-filter button[type="submit"]').click();
  assert.equal(await page.locator('#sales-minimum').evaluate(input=>input.validity.rangeUnderflow),true);
  await page.locator('#sales-minimum').fill('');
  const reset=await action('/api/mints/flows',{minimum:0},()=>page.locator('#sales-minimum').press('Enter'));
  assert.equal(reset.total,original.total);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,trades:[original.total,filtered.total],bridge:[originalBridge.total,bridge.total],
   checks:['thresholds','pagination','sorting','trade side','CSV','independent filters','auto refresh','empty results','reset','validation','mobile and desktop'],
   screenshots:4},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

