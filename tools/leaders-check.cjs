/* Local-only read-only QA in an isolated browser context. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base='http://127.0.0.1:8790',out=path.resolve('test-results');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const response=await context.request.get(base+'/api/mints/leaders');assert.equal(response.status(),200);
  const data=await response.json();assert(data.ready);
  for(const [side,list] of [['buy','buyers'],['sell','sellers']]){
   assert(data[list].length>0);
   assert.equal(data[list].reduce((sum,row)=>sum+BigInt(row.volume_raw),0n),BigInt(data.summary[side].volume_raw));
   assert.equal(data[list].reduce((sum,row)=>sum+BigInt(row.quote_raw),0n),BigInt(data.summary[side].quote_raw));
   data[list].forEach((row,i)=>{assert.equal(row.rank,i+1);if(i)assert(BigInt(data[list][i-1].volume_raw)>=BigInt(row.volume_raw));});
  }
  await page.goto(base+'/#leaders');
  await page.waitForFunction(()=>document.getElementById('leaders-view').getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('[data-view="leaders"]').getAttribute('aria-current'),'page');
  for(const panel of ['mints-view','trading-view','minters-view'])assert.equal(await page.locator('#'+panel).isVisible(),false);
  await page.screenshot({path:path.join(out,'leaders-light.png'),fullPage:true});
  for(const [side,list] of [['buy','buyers'],['sell','sellers']]){
   const table=page.locator('#leaders-'+side+'-table');
   assert.equal(await table.locator('tbody tr').count(),data[list].length);
   for(const field of ['rank','address','volume','quote','price','swaps']){
    const button=table.locator('th[data-sort="'+field+'"] button');
    await button.click();const first=await table.getAttribute('data-order');
    await button.click();assert.notEqual(await table.getAttribute('data-order'),first);
   }
   await table.locator('th[data-sort="volume"] button').click();
   const ascending=(await table.getAttribute('data-order')).endsWith('_asc');
   const values=await table.locator('td[data-raw]').evaluateAll(es=>es.map(e=>e.dataset.raw));
   for(let i=1;i<values.length;i++)assert(ascending?BigInt(values[i-1])<=BigInt(values[i]):BigInt(values[i-1])>=BigInt(values[i]));
   const addr=data[list][0].address;
   await table.locator('button[data-flow-address="'+addr+'"]').click();
   await page.waitForFunction(()=>document.getElementById('address-dialog').getAttribute('aria-busy')==='false');
   assert.equal(await page.locator('#address-dialog').isVisible(),true);
   assert((await page.locator('#address-link').getAttribute('href')).endsWith(addr));
   assert.equal(await page.locator('#address-trading-panel').isVisible(),true);
   await page.locator('#address-close').click();
  }
  const buyTable=page.locator('#leaders-buy-table'),scroll=buyTable.locator('..');
  await scroll.evaluate(node=>node.scrollTop=250);const top=await scroll.evaluate(node=>node.scrollTop);
  assert(top>100);
  const reload=async()=>{await Promise.all([
   page.waitForResponse(r=>r.url().endsWith('/api/mints/leaders')),
   page.locator('#refresh').click()]);
   await page.waitForFunction(()=>document.getElementById('leaders-view').getAttribute('aria-busy')==='false');
  };
  await reload();assert.equal(await scroll.evaluate(node=>node.scrollTop),top);
  const saved=await page.locator('#leaders-buy-total').innerText();
  await page.route('**/api/mints/leaders',r=>r.fulfill({status:503,body:'test outage'}));
  await reload();assert.match(await page.locator('#leaders-status').innerText(),/Предыдущие данные сохранены/);
  assert.equal(await page.locator('#leaders-buy-total').innerText(),saved);
  await page.unroute('**/api/mints/leaders');await reload();
  for(const side of ['buy','sell'])await page.locator('#leaders-'+side+'-table').locator('..').evaluate(node=>node.scrollTop=0);
  await page.locator('#theme-toggle').click();assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.screenshot({path:path.join(out,'leaders-dark.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.equal(await page.locator('[data-view="leaders"]').isVisible(),true);
  await page.screenshot({path:path.join(out,'leaders-mobile.png'),fullPage:true});
  await page.reload();await page.waitForFunction(()=>document.getElementById('leaders-view').getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('#leaders-view').isVisible(),true);
  const empty={...data,buyers:[],sellers:[],summary:{buy:{volume:'0'},sell:{volume:'0'}}};
  await page.route('**/api/mints/leaders',r=>r.fulfill({status:200,json:empty}));await reload();
  assert.match(await page.locator('#leaders-buy-rows').innerText(),/Пока нет сделок/);
  await page.unroute('**/api/mints/leaders');
  await page.route('**/api/mints/leaders',r=>r.fulfill({status:200,json:{...data,ready:false}}));await reload();
  assert.equal(await page.locator('#leaders-content').isVisible(),false);
  assert.match(await page.locator('#leaders-status').innerText(),/не означает отсутствие/);
  await page.unroute('**/api/mints/leaders');await reload();
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,buyers:data.buyers.length,sellers:data.sellers.length,sortableColumns:12,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
