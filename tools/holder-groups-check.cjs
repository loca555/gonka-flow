/* Read-only UI QA in an isolated Chrome context; never the user's browser profile. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const fmt=value=>{
 const [whole,fraction='']=String(value).split('.');
 return BigInt(whole)===0n&&/[1-9]/.test(fraction)?'< 1':BigInt(whole).toLocaleString('ru-RU');
};
(async()=>{
 const base=process.env.TEST_URL||'http://127.0.0.1:8790',out=path.resolve('test-results');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1150},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const response=page.waitForResponse(r=>r.url().includes('/api/mints/flows?')&&r.status()===200);
  const bridgeResponse=page.waitForResponse(r=>r.url().includes('/api/mints/bridge?')&&r.status()===200);
  await page.goto(base+'/#trading');
  const data=await (await response).json(),bridge=await (await bridgeResponse).json(),info=data.outside_holders;
  assert(info.ready&&data.ready&&bridge.ready);
  assert.equal(info.total_raw,data.summary.outside_raw);
  assert.equal(info.height,data.snapshot.height);
  assert.equal(info.groups.length,4);
  assert.equal(info.groups.reduce((n,g)=>n+BigInt(g.balance_raw),0n),BigInt(info.total_raw));
  const holders=info.groups.flatMap(g=>g.holders);
  assert.equal(holders.length,info.addresses);
  assert.equal(new Set(holders.map(h=>h.address)).size,holders.length);
  for(const group of info.groups){
   assert.equal(group.holders.length,group.addresses);
   assert.equal(group.holders.reduce((n,h)=>n+BigInt(h.balance_raw),0n),BigInt(group.balance_raw));
   for(const h of group.holders){
    const buy=BigInt(h.bought_raw),sell=BigInt(h.sold_raw),total=buy+sell;
    assert(BigInt(h.balance_raw)>0n);
    assert(!data.pools.some(p=>p.address===h.address));
    const expected=!total?'unclassified':buy*10n>total*9n?'investors':sell*10n>total*9n?'sellers':'traders';
    assert.equal(group.id,expected);
   }
  }
  await page.locator('[data-holder-group="investors"]').waitFor();
  for(const group of info.groups){
   const card=page.locator('[data-holder-group="'+group.id+'"]');
   assert.equal(await card.getAttribute('data-balance-raw'),group.balance_raw);
  }
  await page.locator('.distribution-panel').screenshot({path:path.join(out,'holder-groups-light.png')});
  for(const group of info.groups){
   const card=page.locator('[data-holder-group="'+group.id+'"]');
   await card.click();await page.locator('#holder-dialog').waitFor();
   assert.equal(await page.locator('#holder-rows tr[data-holder-address]').count(),group.addresses);
   if(group.addresses){
    assert.equal(await page.locator('#holder-rows tr[data-holder-address]').first().getAttribute('data-holder-address'),group.holders[0].address);
    for(const field of ['address','balance','share','bought','sold','buyshare']){
     const header=page.locator('#holder-table th[data-sort="'+field+'"]');
     await header.locator('button').click();const first=await header.getAttribute('aria-sort');
     await header.locator('button').click();assert.notEqual(await header.getAttribute('aria-sort'),first);
    }
    if(group.id!=='unclassified'){
     const button=page.locator('#holder-rows [data-flow-address]').first(),address=await button.getAttribute('data-flow-address');
     await button.click();await page.locator('#address-data').waitFor();
     assert((await page.locator('#address-link').innerText()).includes(address));
     await page.locator('#address-chart svg').waitFor();
     assert(await page.locator('#address-rows tr').count()>0);
     assert(await page.locator('#holder-dialog').evaluate(d=>d.open));
     await page.locator('#address-close').click();await page.locator('#address-dialog').waitFor({state:'hidden'});
     assert(await page.locator('#holder-dialog').isVisible());
    }
   }
   if(group.id==='investors')await page.locator('#holder-dialog').screenshot({path:path.join(out,'holder-list-light.png')});
   await page.keyboard.press('Escape');await page.locator('#holder-dialog').waitFor({state:'hidden'});
  }
  // The same investor dialog is keyboard-accessible and dismissible on its backdrop.
  await page.locator('[data-holder-group="investors"]').focus();await page.keyboard.press('Enter');
  const box=await page.locator('#holder-dialog').boundingBox();
  await page.mouse.click(Math.max(2,box.x/2),box.y+25);
  await page.locator('#holder-dialog').waitFor({state:'hidden'});
  await page.locator('#theme-toggle').click();
  await page.locator('.distribution-panel').screenshot({path:path.join(out,'holder-groups-dark.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#outside-holders').screenshot({path:path.join(out,'holder-groups-mobile.png')});
  await page.locator('[data-holder-group="traders"]').click();
  assert(await page.locator('#holder-dialog').evaluate(d=>d.scrollWidth<=d.clientWidth));
  assert(await page.locator('#holder-dialog').evaluate(d=>d.getBoundingClientRect().height<=innerHeight));
  await page.locator('#holder-dialog').screenshot({path:path.join(out,'holder-list-mobile.png')});
  await page.locator('#holder-close').click();
  await page.setViewportSize({width:1440,height:1150});
  await page.locator('#theme-toggle').click();
  // Failed refresh retains verified cards; unavailable data is not presented as zero.
  const saved=await page.locator('#holder-group-cards').innerText();
  await page.route('**/api/mints/flows?**',r=>r.fulfill({status:503,body:'test source outage'}));
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.getElementById('trade-status').textContent.includes('Нет свежего ответа'));
  assert.equal(await page.locator('#holder-group-cards').innerText(),saved);
  await page.unroute('**/api/mints/flows?**');
  await page.route('**/api/mints/flows?**',r=>r.fulfill({json:{...data,outside_holders:{ready:false}}}));
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.getElementById('outside-holders-content').hidden);
  assert.match(await page.locator('#outside-holders-total').innerText(),/не подтверждена/);
  await page.unroute('**/api/mints/flows?**');
  // Numeric minter labels use all-history totals, while burn rows keep their own label.
  await page.locator('[data-view="bridge"]').click();
  await page.locator('#mint-rows .minter-sales-summary').first().waitFor();
  const actualBridge=await (await context.request.get(base+'/api/mints/bridge?minimum=0&limit=50')).json();
  for(const item of actualBridge.items){
   const row=page.locator('#mint-rows tr').filter({has:page.locator('[data-tx="'+item.tx_hash+'"][data-log="'+item.log_index+'"]')});
   if(!await row.count())continue;
   if(item.kind==='bridge_mint'){
    const label=row.locator('.minter-sales-summary'),totals=item.minter_totals;
    assert.equal(await label.innerText(),'Слил '+fmt(totals.sold)+' из '+fmt(totals.minted)+' WGNK');
    assert.equal(await label.getAttribute('data-sold-raw'),totals.sold_raw);
    assert.equal(await label.getAttribute('data-minted-raw'),totals.minted_raw);
    assert.match(await label.getAttribute('title'),/Конкретные партии токенов не отслеживаются/);
   }else assert.match(await row.innerText(),/Адрес сжигания/);
  }
  await page.locator('#mint-rows tr').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'minter-sales-light.png')});
  const mint=actualBridge.items.find(e=>e.kind==='bridge_mint'),sample={...actualBridge,total:1,has_more:false,items:[{...mint,minter_totals:null}]};
  await page.route('**/api/mints/bridge?**',r=>r.fulfill({json:sample}));
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.getElementById('mint-rows').textContent.includes('Продажи / чеканка —'));
  await page.unroute('**/api/mints/bridge?**');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,height:info.height,total:info.total,groups:info.groups.map(({id,balance,addresses})=>({id,balance,addresses})),minterLabels:true,sorting:true,addressCharts:true,mobile:true,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
