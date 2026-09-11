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
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{channel:'chrome'})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#leaders');await page.locator('#leaders-buy-chart .price-histogram').waitFor();
  const check=async step=>{
   const axes=[];
   for(const side of ['buy','sell']){
    const chart=page.locator('#leaders-'+side+'-chart');
    const bars=await chart.locator('.price-volume').evaluateAll(nodes=>nodes.map(n=>({...n.dataset})));
    const expected=data.price_distribution[step][side];
    assert.equal(bars.length,expected.length);assert(bars.every(b=>b.kind===side));
    assert.deepEqual(bars.map(b=>({from:b.from,to:b.to,raw:b.raw})),expected.map(b=>({from:b.from_price_raw,to:b.to_price_raw,raw:b.volume_raw})));
    assert.equal(await chart.locator('.market-price').count(),0);
    axes.push(await chart.locator('svg > text:not([class])').allTextContents());
    assert((await page.locator('#leaders-'+side+'-price-summary').innerText()).includes('Больше всего объёма'));
   }
   assert.deepEqual(axes[0],axes[1]);
  };
  await check('5');const summary=await page.locator('#leaders-buy-price-summary').innerText();
  await page.locator('#leaders-price-step').selectOption('10');await check('10');
  assert.notEqual(await page.locator('#leaders-buy-price-summary').innerText(),summary);
  await page.locator('#leaders-price-step').selectOption('5');await check('5');
  const svg=page.locator('#leaders-buy-chart svg');await svg.focus();await svg.press('Home');
  assert(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible());
  await svg.press('End');assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('Исполнений:'));
  await svg.press('Escape');assert(!(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible()));
  await page.locator('#leaders-price-step').focus();await page.evaluate(()=>window.scrollTo(0,0));
  await fs.mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/price-bands-desktop.jpg',type:'jpeg',quality:82});
  await page.locator('#theme-toggle').click();
  await page.screenshot({path:'test-results/price-bands-dark.jpg',type:'jpeg',quality:80});
  await page.setViewportSize({width:390,height:900});
  await page.waitForFunction(()=>document.querySelector('#leaders-buy-chart svg').viewBox.baseVal.width<400);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert(await page.locator("#leaders-buy-chart svg > text:not([class])").evaluateAll(nodes=>nodes.every(node=>node.getBBox().x>=0)),"Volume labels must stay inside mobile chart");
  const panel=page.locator('.leader-panel.buy .leader-chart-panel');await panel.scrollIntoViewIfNeeded();
  await panel.screenshot({path:'test-results/price-bands-mobile.jpg',type:'jpeg',quality:85});
  await svg.click({position:{x:100,y:100}});assert(await page.locator('#leaders-buy-chart .chart-tooltip').isVisible());
  await page.route('**/api/mints/leaders',route=>route.fulfill({status:503,body:'test outage'}));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('#leaders-status').textContent.includes('Предыдущие данные сохранены'));
  await check('5');await page.unroute('**/api/mints/leaders');
  const make=(from,to)=>({from_price_raw:from,to_price_raw:to,volume_raw:'1000000000',volume:'1',quote_raw:'100000',quote:'0.1',swaps:1,price_raw:from});
  const bands={buy:[make('100000000000','150000000000'),make('200000000000','250000000000')],sell:[]};
  const fixture={...data,price_distribution:{'5':bands,'10':bands}};
  await page.route('**/api/mints/leaders',route=>route.fulfill({json:fixture}));
  await page.reload();await page.locator('#leaders-buy-chart svg').waitFor();
  await page.locator('#leaders-buy-chart svg').press('Home');await page.locator('#leaders-buy-chart svg').press('ArrowRight');
  assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('0,15–0,20'));
  assert((await page.locator('#leaders-buy-chart .chart-tooltip').innerText()).includes('Исполнений: 0'));
  assert.equal(await page.locator('#leaders-sell-chart svg').count(),0);
  assert((await page.locator('#leaders-sell-chart').innerText()).includes('пока нет'));
  await page.unroute('**/api/mints/leaders');
  await page.route('**/api/mints/leaders',route=>route.fulfill({json:{...data,ready:false,price_distribution:null}}));
  await page.reload();await page.waitForFunction(()=>document.querySelector('#leaders-view').getAttribute('aria-busy')==='false');
  assert(!(await page.locator('#leaders-content').isVisible()));assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,snapshot:data.snapshot.height,buyBands:data.price_distribution['5'].buy.length,sellBands:data.price_distribution['5'].sell.length,consoleErrors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
