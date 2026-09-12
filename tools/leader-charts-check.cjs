/* Local price distribution QA with real API data and controlled empty/error responses. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.LEADERS_TEST_BASE||'http://127.0.0.1:8796';
assert(['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname),'Use a local test server');
(async()=>{
 const response=await fetch(base+'/api/mints/leaders');assert.equal(response.status,200);
 const data=await response.json();assert(data.ready);
 for(const step of ['5','10'])for(const side of ['buy','sell']){
  const bins=data.price_distribution[step][side];assert(bins.length>0);
  for(const key of ['volume_raw','quote_raw','swaps'])assert.equal(bins.reduce((sum,b)=>sum+BigInt(b[key]),0n),BigInt(data.summary[side][key]));
  for(const b of bins){assert.equal(BigInt(b.price_raw),BigInt(b.quote_raw)*10n**15n/BigInt(b.volume_raw));assert.equal(BigInt(b.to_price_raw)-BigInt(b.from_price_raw),BigInt(step)*10n**10n);}
 }
 assert(data.bridge.ready);
 for(const step of ['5','10']){
  assert.equal(data.price_days[step].bands.reduce((sum,b)=>sum+b.days,0),data.price_days[step].total_days);
  for(const key of ['gross_in_raw','in_raw','out_raw','pool_funding_raw'])assert.equal(data.price_bridge[step].bands.reduce((sum,b)=>sum+BigInt(b[key]),BigInt(data.price_bridge[step].unassigned[key])),BigInt(data.bridge.totals[key]));
 }
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#leaders');await page.locator('#leaders-buy-chart .price-histogram').waitFor();
  const check=async step=>{
   const axes=[],countAxes=[];
   for(const side of ['buy','sell']){
    const chart=page.locator('#leaders-'+side+'-chart');
    const bars=await chart.locator('.price-volume').evaluateAll(nodes=>nodes.map(n=>({...n.dataset})));
    const expected=data.price_distribution[step][side];
    assert.equal(bars.length,expected.length);assert(bars.every(b=>b.kind===side));
    assert.deepEqual(bars.map(b=>({from:b.from,to:b.to,raw:b.raw})),expected.map(b=>({from:b.from_price_raw,to:b.to_price_raw,raw:b.volume_raw})));
    assert.equal(await chart.locator('.market-price').count(),0);
    assert.equal(await chart.locator('.price-count-line').count(),1);
    const points=await chart.locator('.price-count-point').evaluateAll(nodes=>nodes.map(n=>({...n.dataset})));
    const dayCounts=new Map(data.price_days[step].bands.map(b=>[b.from_price_raw,b.days]));
    assert.deepEqual(points.filter(p=>p.swaps!=='0').map(p=>({from:p.from,swaps:Number(p.swaps)})),expected.map(b=>({from:b.from_price_raw,swaps:b.swaps})));
    assert(points.every(p=>Number(p.days)===(dayCounts.get(p.from)||0)));
    countAxes.push(await chart.locator('text.price-count-axis').allTextContents());
    axes.push(await chart.locator('svg > text:not([class])').allTextContents());
    const graphic=chart.locator('svg'),tip=chart.locator('.price-band-tooltip');
    await graphic.focus();await graphic.press('Home');
    for(const point of points){
     const bridge=data.price_bridge[step].bands.find(b=>b.from_price_raw===point.from);
     assert.equal(await tip.getAttribute('data-from'),point.from);
     assert.equal(await tip.getAttribute('data-bridge-in'),bridge?.in_raw||'0');
     assert.equal(await tip.getAttribute('data-bridge-out'),bridge?.out_raw||'0');
     assert((await tip.innerText()).includes('Мост в эти дни'));
     await graphic.press('ArrowRight');
    }
    await graphic.press('Escape');
    assert((await page.locator('#leaders-'+side+'-price-summary').innerText()).includes('Больше всего объёма'));
   }
   assert.deepEqual(axes[0],axes[1]);assert.deepEqual(countAxes[0],countAxes[1]);
  };
  const bridgeNote=page.locator('#leaders-bridge-note');
  assert.equal(await bridgeNote.getAttribute('data-excluded'),data.bridge.adjustment.amount_raw);
  await bridgeNote.locator('summary').click();
  assert.equal(await bridgeNote.locator('a[href*="/tx/"]').count(),data.bridge.adjustment.deposits.length);
  assert((await bridgeNote.innerText()).includes('исходные переводы сохранены'));
  await bridgeNote.locator('summary').click();
  await check('5');const summary=await page.locator('#leaders-buy-price-summary').innerText();
  await page.locator('#leaders-price-step').selectOption('10');await check('10');
  assert.notEqual(await page.locator('#leaders-buy-price-summary').innerText(),summary);
  await page.locator('#leaders-price-step').selectOption('5');await check('5');
  const svg=page.locator('#leaders-buy-chart svg');await svg.focus();await svg.press('Home');
  assert(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible());
  await svg.press('End');assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('Дней на уровне'));
  await svg.press('Escape');assert(!(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible()));
  for(const side of ['buy','sell']){
   const chart=page.locator('#leaders-'+side+'-chart'),tip=chart.locator('.chart-tooltip');
   const row=data.price_distribution['5'][side][0],point=chart.locator('.price-count-point[data-from="'+row.from_price_raw+'"]');
   const box=await point.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
   assert(await tip.isVisible());assert.equal(await tip.getAttribute('data-swaps'),String(row.swaps));
   assert.equal(await tip.getAttribute('data-days'),String(data.price_days['5'].bands.find(b=>b.from_price_raw===row.from_price_raw)?.days||0));
  }
  await fs.mkdir('test-results',{recursive:true});
  const buyPanel=page.locator('.leader-panel.buy .leader-chart-panel');
  await buyPanel.scrollIntoViewIfNeeded();await svg.focus();await svg.press('Home');
  await buyPanel.screenshot({path:'test-results/price-bands-bridge-tooltip.jpg',type:'jpeg',quality:88});
  await page.locator('#leaders-price-step').focus();await page.evaluate(()=>window.scrollTo(0,0));await page.mouse.move(20,70);
  await fs.mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/price-bands-desktop.jpg',type:'jpeg',quality:82});
  await page.locator('#theme-toggle').click();
  await page.screenshot({path:'test-results/price-bands-dark.jpg',type:'jpeg',quality:80});
  await page.setViewportSize({width:390,height:900});
  await page.waitForFunction(()=>document.querySelector('#leaders-buy-chart svg').viewBox.baseVal.width<400);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert(await page.locator("#leaders-buy-chart svg > text:not([class])").evaluateAll(nodes=>nodes.every(node=>node.getBBox().x>=0)),"Volume labels must stay inside mobile chart");
  assert(await page.locator('#leaders-buy-chart text.price-count-axis').evaluateAll(nodes=>nodes.every(n=>n.getBBox().x+n.getBBox().width<=n.ownerSVGElement.viewBox.baseVal.width)),"Count labels must stay inside mobile chart");
  const panel=page.locator('.leader-panel.buy .leader-chart-panel');await panel.scrollIntoViewIfNeeded();
  await panel.screenshot({path:'test-results/price-bands-mobile.jpg',type:'jpeg',quality:85});
  await svg.click({position:{x:100,y:100}});assert(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible());
  assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('Дней на уровне'));
  const mobileTip=await page.locator('#leaders-buy-chart .price-band-tooltip').boundingBox(),mobileChart=await svg.boundingBox();
  assert(mobileTip.x>=mobileChart.x&&mobileTip.x+mobileTip.width<=mobileChart.x+mobileChart.width,'Tooltip fits mobile width');
  assert(mobileTip.y>=mobileChart.y&&mobileTip.y+mobileTip.height<=mobileChart.y+mobileChart.height,'Tooltip fits mobile height');
  await panel.screenshot({path:'test-results/price-bands-mobile-tooltip.jpg',type:'jpeg',quality:85});
  await page.route('**/api/mints/leaders',route=>route.fulfill({status:503,body:'test outage'}));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('#leaders-status').textContent.includes('Предыдущие данные сохранены'));
  await check('5');await page.unroute('**/api/mints/leaders');
  const make=(from,to)=>({from_price_raw:from,to_price_raw:to,volume_raw:'1000000000',volume:'1',quote_raw:'100000',quote:'0.1',swaps:1,price_raw:from});
  const bands={buy:[make('100000000000','150000000000'),make('200000000000','250000000000')],sell:[]};
  const dayFixture={total_days:2,bands:bands.buy.map(b=>({from_price_raw:b.from_price_raw,to_price_raw:b.to_price_raw,days:1}))};
  const emptyBridge={bands:[],unassigned:{days:0,in_raw:'0',out_raw:'0'}};
  const fixture={...data,price_distribution:{'5':bands,'10':bands},price_days:{'5':dayFixture,'10':dayFixture},price_bridge:{'5':emptyBridge,'10':emptyBridge}};
  await page.route('**/api/mints/leaders',route=>route.fulfill({json:fixture}));
  await page.reload();await page.locator('#leaders-buy-chart svg').waitFor();
  await page.locator('#leaders-buy-chart svg').press('Home');await page.locator('#leaders-buy-chart svg').press('ArrowRight');
  assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('0,15–0,20'));
  assert.equal(await page.locator('#leaders-buy-chart .chart-tooltip').getAttribute('data-swaps'),'0');
  assert.equal(await page.locator('#leaders-buy-chart .chart-tooltip').getAttribute('data-days'),'0');
  assert.equal(await page.locator('#leaders-buy-chart .chart-tooltip').getAttribute('data-bridge-in'),'0');
  assert.equal(await page.locator('#leaders-sell-chart svg').count(),0);
  assert((await page.locator('#leaders-sell-chart').innerText()).includes('пока нет'));
  await page.unroute('**/api/mints/leaders');
  await page.route('**/api/mints/leaders',route=>route.fulfill({json:{...data,bridge:{ready:false},price_bridge:{'5':null,'10':null}}}));
  await page.reload();await page.locator('#leaders-buy-chart svg').waitFor();
  await page.locator('#leaders-buy-chart svg').press('Home');
  assert.equal(await page.locator('#leaders-buy-chart .chart-tooltip').getAttribute('data-bridge-in'),'');
  assert((await page.locator('#leaders-buy-chart .price-bridge-stats').innerText()).includes('Данные пока не подтверждены'));
  await page.unroute('**/api/mints/leaders');
  await page.route('**/api/mints/leaders',route=>route.fulfill({json:{...data,ready:false,price_distribution:null}}));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#leaders-view').getAttribute('aria-busy')==='false');
  assert(!(await page.locator('#leaders-content').isVisible()));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,snapshot:data.snapshot.height,buyBands:data.price_distribution['5'].buy.length,sellBands:data.price_distribution['5'].sell.length,totalDays:data.price_days['5'].total_days,consoleErrors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
