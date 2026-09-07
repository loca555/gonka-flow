/* Isolated, read-only localhost UI check. Never opens the user's browser profile. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const base=process.env.HEADER_TEST_BASE||'http://127.0.0.1:8790';
function displayed(value){
 const [whole,fraction='']=value.split('.');
 const raw=BigInt(whole)*10n**12n+BigInt(fraction.padEnd(12,'0'));
 const n=(raw+500000000n)/1000000000n;
 const f=(n%1000n).toString().padStart(3,'0');
 return '$'+(n/1000n).toLocaleString('ru-RU')+'.'+f;
}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:950}}),errors=[],widths=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>localStorage.setItem('gonka-flow-theme','light'));
  await page.route('http://127.0.0.1:8791/**',route=>route.abort());
  const out=path.resolve('test-results','header-price');await fs.mkdir(out,{recursive:true});
  const isFlow=response=>response.url().startsWith(base+'/api/mints/flows?');
  const response=page.waitForResponse(isFlow);
  await page.goto(base+'/#trading');
  let data=await (await response).json();
  assert(data.ready&&data.latest_trade,'local snapshot must contain a last trade');
  async function verifyPrice(value){
   await page.waitForFunction(expected=>document.getElementById('header-price-value')?.textContent===expected,value);
   assert.equal(await page.locator('#header-price-value').textContent(),value);
  }
  await verifyPrice(displayed(data.latest_trade.price));
  assert.equal(await page.locator('#header-price>span').count(),0,'no visible unit label');
  assert.equal(await page.locator('#header-price').innerText(),displayed(data.latest_trade.price));
  assert.match(await page.locator('#header-price-value').getAttribute('aria-label'),/Цена за 1 WGNK, USDT/);
  assert.equal(data.trades[0].price,data.latest_trade.price,'header matches newest all-side execution');
  await page.locator('.masthead').screenshot({path:path.join(out,'light.png')});
  await page.locator('#theme-toggle').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.locator('.masthead').screenshot({path:path.join(out,'dark.png')});
  for(const width of [1440,1280,1200,1024,760,600,390,320]){
   await page.setViewportSize({width,height:950});
   const layout=await page.evaluate(()=>{
    const box=id=>{const r=document.getElementById(id).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};};
    return {width:innerWidth,body:document.body.scrollWidth,price:box('header-price'),theme:box('theme-toggle'),refresh:box('refresh')};
   });
   assert(layout.body<=width+1,JSON.stringify(layout));
   assert(layout.price.right<=layout.theme.left,JSON.stringify(layout));
   assert(layout.refresh.right<=width,JSON.stringify(layout));
   assert(layout.price.top<layout.theme.bottom&&layout.theme.top<layout.price.bottom,JSON.stringify(layout));
   widths.push(width);
  }
  await page.locator('.masthead').screenshot({path:path.join(out,'mobile.png')});
  await page.setViewportSize({width:1440,height:950});
  async function action(callback){
   const response=page.waitForResponse(isFlow);await callback();
   data=await (await response).json();await verifyPrice(displayed(data.latest_trade.price));
  }
  await page.locator('#sales-query').fill('0x'+'f'.repeat(40));
  await action(()=>page.locator('[data-trade-side="buy"]').click());
  assert.equal(data.total,0);assert(data.latest_trade,'price survives an empty address search');
  await action(()=>page.locator('#sales-reset').click());
  await action(()=>page.locator('#trades-table th[data-sort="time"] button').click());
  assert.equal(data.sort,'time_asc');
  const lastPrice=displayed(data.latest_trade.price);
  await page.route('**/api/mints/flows?**',route=>route.fulfill({status:503,body:'unavailable'}));
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.getElementById('header-price').dataset.state==='stale');
  await verifyPrice(lastPrice);
  assert.match(await page.locator('#header-price').getAttribute('title'),/Нет свежего ответа/);
  await page.unroute('**/api/mints/flows?**');
  await action(()=>page.locator('#refresh').click());
  await page.route('**/api/mints/flows?**',route=>route.fulfill({json:{...data,ready:false,latest_trade:null}}));
  await page.locator('#refresh').click();await verifyPrice('—');
  await page.unroute('**/api/mints/flows?**');
  await action(()=>page.locator('#refresh').click());
  for(const [raw,expected] of [['0.127115','$0.127'],['0.1275','$0.128'],['0.1','$0.100'],['0.999999999999','$1.000'],['0','$0.000']]){
   await page.route('**/api/mints/flows?**',route=>route.fulfill({json:{...data,latest_trade:{...data.latest_trade,price:raw}}}));
   await page.locator('#refresh').click();await verifyPrice(expected);
   await page.unroute('**/api/mints/flows?**');
  }
  await action(()=>page.locator('#refresh').click());
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,widths,price:displayed(data.latest_trade.price),
   latestTrade:data.latest_trade,filtersAndSort:true,failureAndRecovery:true,errors,screenshots:out},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
