/* Local-only report period QA in a separate browser profile. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.LEADERS_TEST_BASE||'http://127.0.0.1:8796';
assert(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Use a local test server');
(async()=>{
 const load=async day=>{const r=await fetch(base+'/api/mints/leaders'+(day?'?start_date='+day:''));assert.equal(r.status,200);return r.json();};
 const all=await load(''),september=await load('2026-09-01');
 assert(all.ready&&september.ready&&september.bridge.ready);
 assert.equal(september.start_date,'2026-09-01');
 assert.equal(september.since,Date.parse('2026-08-31T21:00:00Z')/1000);
 assert.equal(september.bridge.adjustment.amount_raw,'0');
 for(const [side,list] of [['buy','buyers'],['sell','sellers']]){
  assert(BigInt(september.summary[side].volume_raw)>0n);
  assert(BigInt(september.summary[side].volume_raw)<BigInt(all.summary[side].volume_raw));
  assert(september[list].every(r=>r.first_ts>=september.since));
  for(const key of ['volume_raw','quote_raw','swaps']){
   const expected=BigInt(september.summary[side][key]);
   assert.equal(september[list].reduce((sum,r)=>sum+BigInt(r[key]),0n),expected);
   for(const step of ['5','10'])assert.equal(september.price_distribution[step][side].reduce((sum,r)=>sum+BigInt(r[key]),0n),expected);
  }
 }
 for(const step of ['5','10']){
  const series=september.price_bridge[step];
  for(const key of Object.keys(september.bridge.totals))assert.equal(series.bands.reduce((sum,r)=>sum+BigInt(r[key]),BigInt(series.unassigned[key])),BigInt(september.bridge.totals[key]));
 }
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100},reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const isPeriod=(r,day)=>{const u=new URL(r.url());return u.pathname==='/api/mints/leaders'&&(u.searchParams.get('start_date')||'')===day;};
  const settled=()=>page.waitForFunction(()=>document.getElementById('leaders-view').getAttribute('aria-busy')==='false');
  const apply=async day=>{
   await page.locator('#leaders-start-date').fill(day);
   const response=page.waitForResponse(r=>isPeriod(r,day));
   await page.getByRole('button',{name:'Применить',exact:true}).click();
   await response;await settled();
  };
  const checkRows=async data=>{
   for(const [side,list] of [['buy','buyers'],['sell','sellers']]){
    const values=await page.locator('#leaders-'+side+'-rows td[data-raw]').evaluateAll(nodes=>nodes.map(n=>n.dataset.raw));
    assert.equal(values.length,data[list].length);
    assert.equal(values.reduce((sum,v)=>sum+BigInt(v),0n),BigInt(data.summary[side].volume_raw));
   }
  };
  await page.goto(base+'/#leaders');await page.locator('#leaders-buy-chart svg').waitFor();
  await checkRows(all);assert.equal(await page.locator('#leaders-period').innerText(),'Вся история');
  await page.waitForFunction(()=>document.title.includes(' · WGNK — Gonka Flow'));
  const quote=await page.title();
  const calendar=page.locator('#leaders-start-date');
  await calendar.fill('2026-09-14');await calendar.click({position:{x:25,y:20}});
  assert(await calendar.evaluate(e=>e.matches(':open')),'calendar must open on the text area');
  await page.keyboard.press('ArrowLeft');await page.keyboard.press('Enter');
  assert.equal(await calendar.inputValue(),'2026-09-13');
  assert(await calendar.evaluate(e=>!e.matches(':open')));
  await calendar.fill('');
  await apply('2026-09-01');await checkRows(september);
  assert.equal(await page.locator('#leaders-period').innerText(),'С 01.09.2026');
  assert.equal(await page.locator('#leaders-buy-table tbody tr').first().getAttribute('data-address'),september.buyers[0].address);
  assert((await page.locator('#leaders-buy-price-summary').innerText()).includes('с 01.09.2026'));
  assert(!(await page.locator('#leaders-buy-price-summary').innerText()).includes('вся история'));
  await page.locator('#leaders-price-step').selectOption('10');
  await page.locator('#leaders-buy-chart svg').press('Home');
  assert.equal(await page.locator('#leaders-buy-chart .chart-tooltip').getAttribute('data-bridge-in'),september.price_bridge['10'].bands[0].in_raw);
  await page.locator('#leaders-buy-table th[data-sort="swaps"] button').click();
  await checkRows(september);
  await page.getByRole('link',{name:'Торговля',exact:true}).click();
  await page.getByRole('link',{name:'Крупные трейдеры',exact:true}).click();
  await settled();assert.equal(await page.locator('#leaders-start-date').inputValue(),'2026-09-01');
  await page.reload();await page.locator('#leaders-buy-chart svg').waitFor();
  assert.equal(await page.locator('#leaders-start-date').inputValue(),'2026-09-01');await checkRows(september);
  assert.equal(await page.title(),quote);
  // A failed new period must never show the previous period's amounts.
  const failed='**/api/mints/leaders?start_date=2026-09-02';
  await page.route(failed,r=>r.fulfill({status:503,body:'test outage'}));
  await apply('2026-09-02');
  assert.equal(await page.locator('#leaders-content').isVisible(),false);
  assert.equal(await page.locator('#leaders-period-form').isVisible(),true);
  assert((await page.locator('#leaders-status').innerText()).includes('Не удалось'));
  await page.unroute(failed);
  await apply('2026-09-02');assert.equal(await page.locator('#leaders-content').isVisible(),true);
  // An older, slow request may finish only after a newer period was selected.
  let unblock,arrived;const gate=new Promise(resolve=>unblock=resolve),held=new Promise(resolve=>arrived=resolve);
  await page.route('**/api/mints/leaders?start_date=2026-08-01',async route=>{arrived();await gate;await route.fulfill({json:all}).catch(()=>{});});
  await page.locator('#leaders-start-date').fill('2026-08-01');
  await page.getByRole('button',{name:'Применить',exact:true}).click();await held;
  await apply('2026-09-01');unblock();await checkRows(september);
  await page.unroute('**/api/mints/leaders?start_date=2026-08-01');
  assert.equal(await page.locator('#leaders-period').innerText(),'С 01.09.2026');
  await apply('2099-01-01');
  assert.equal(await page.locator('#leaders-content').isVisible(),true);
  assert.equal(await page.locator('#leaders-buy-chart svg').count(),0);
  assert((await page.locator('#leaders-buy-rows').innerText()).includes('за выбранный период'));
  const resetResponse=page.waitForResponse(r=>isPeriod(r,''));
  await page.getByRole('button',{name:'Вся история',exact:true}).click();await resetResponse;await settled();
  await checkRows(all);assert.equal(await page.locator('#leaders-start-date').inputValue(),'');
  await page.reload();await page.locator('#leaders-buy-chart svg').waitFor();await checkRows(all);
  await apply('2026-09-01');
  await fs.mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/leaders-date-desktop.png'});
  await page.locator('#theme-toggle').click();
  await page.screenshot({path:'test-results/leaders-date-dark.png'});
  for(const width of [390,320]){
   await page.setViewportSize({width,height:950});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'horizontal overflow at '+width);
   assert(await page.getByRole('button',{name:'Применить',exact:true}).isVisible());
   await page.screenshot({path:'test-results/leaders-date-'+width+'.png'});
  }
  assert.deepEqual(errors,[]);
  const result={ok:true,snapshot:september.snapshot.height,from:september.start_date,buy:september.summary.buy.volume,sell:september.summary.sell.volume,days:september.price_days['5'].total_days,bridge:september.bridge.totals,calendarClick:true,filterAndReset:true,persistsAfterReload:true,failedPeriodHidden:true,requestRace:true,errors};
  await fs.writeFile('test-results/leaders-date.json',JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
