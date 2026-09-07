/* Read-only UI regression in an isolated Chrome profile; API fixtures stay in memory. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.AUTO_REFRESH_TEST_BASE||'http://127.0.0.1:8790';
const account='0x0a34924dd04ac5e5589c5e849c95d927bfd50bd1';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:950}}),errors=[],cache=new Map();
  let mode='normal',flowResponses=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://127.0.0.1:8791/**',route=>route.abort());
  await page.route(base+'/api/**',async route=>{
   const url=route.request().url(),isFlow=url.includes('/api/mints/flows?');
   assert.equal(route.request().method(),'GET');
   if(!cache.has(url)){
    const response=await route.fetch();
    assert(response.ok(),'fixture response: '+response.status());
    cache.set(url,await response.json());
   }
   const data=structuredClone(cache.get(url));
   if(isFlow){
    flowResponses++;
    if(mode==='changed')data.latest_trade.price='0.246';
   }
   await route.fulfill({json:data});
  });
  await page.goto(base+'/#trading');
  await page.locator('#flow-content').waitFor({state:'visible'});
  await page.locator('#sales-chart svg').waitFor();
  assert.equal(await page.locator('#refresh').count(),0);
  await page.locator('#sales-query').fill(account);
  await page.locator('[data-trade-side="all"]').click();
  await page.waitForFunction(address=>document.getElementById('sales-result').textContent.includes(address),account);
  await page.locator('#trades-table th[data-sort="time"] button').click();
  await page.waitForFunction(()=>document.getElementById('trades-table').dataset.order==='time_asc');
  await page.locator('#sales-query').fill('несохранённый текст');
  const rows=page.locator('#sales-rows [data-flow-address]');
  assert(await rows.count()>0,'address must have trades');
  await rows.first().click();
  await page.locator('#address-dialog[open] #address-chart svg').waitFor();
  await page.locator('#address-trades-table th[data-sort="time"] button').click();
  await page.waitForFunction(()=>document.getElementById('address-trades-table').dataset.order==='time_asc');
  await page.evaluate(()=>{
   const dialog=document.getElementById('address-dialog'),table=dialog.querySelector('.address-table');
   dialog.scrollTop=350;table.scrollTop=250;
   window.__quiet={
    row:document.getElementById('sales-rows').firstChild,
    chart:document.querySelector('#sales-chart svg'),
    addressRow:document.getElementById('address-rows').firstChild,
    addressChart:document.querySelector('#address-chart svg'),
    top:dialog.scrollTop,tableTop:table.scrollTop,loading:[]
   };
   const observer=new MutationObserver(()=>{
    for(const id of ['sales-result','address-status']){
     const text=document.getElementById(id).textContent;
     if(/Ищем|Загружаем покупки/.test(text))window.__quiet.loading.push(text);
    }
   });
   for(const id of ['sales-result','address-status'])observer.observe(document.getElementById(id),{childList:true,subtree:true,characterData:true});
  });
  const before=flowResponses;mode='changed';
  // This first update uses the real production 20-second timer, with no button/event trigger.
  await page.waitForFunction(()=>document.getElementById('header-price-value').textContent==='$0.246',null,{timeout:35000});
  assert(flowResponses>before,'native background timer requested fresh data');
  // Returning to the tab also refreshes the open address without resetting the dialog.
  const addressReply=page.waitForResponse(response=>response.url().includes('/api/mints/address/'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await addressReply;
  await page.waitForFunction(()=>document.getElementById('address-dialog').getAttribute('aria-busy')==='false');
  const state=await page.evaluate(()=>{
   const q=window.__quiet,dialog=document.getElementById('address-dialog');
   return {open:dialog.open,top:dialog.scrollTop,tableTop:dialog.querySelector('.address-table').scrollTop,
    expectedTop:q.top,expectedTableTop:q.tableTop,
    sameRows:q.row===document.getElementById('sales-rows').firstChild,
    sameChart:q.chart===document.querySelector('#sales-chart svg'),
    sameAddressRows:q.addressRow===document.getElementById('address-rows').firstChild,
    sameAddressChart:q.addressChart===document.querySelector('#address-chart svg'),
    query:document.getElementById('sales-query').value,
    sort:document.getElementById('trades-table').dataset.order,
    addressSort:document.getElementById('address-trades-table').dataset.order,loading:q.loading};
  });
  assert(state.open&&state.sameRows&&state.sameChart&&state.sameAddressRows&&state.sameAddressChart,JSON.stringify(state));
  assert.equal(state.top,state.expectedTop);assert.equal(state.tableTop,state.expectedTableTop);
  assert.equal(state.query,'несохранённый текст');assert.equal(state.sort,'time_asc');assert.equal(state.addressSort,'time_asc');
  assert.deepEqual(state.loading,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,nativeTimer:true,flowResponses,state,errors},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
