// Standalone UI regression check with an isolated browser and controlled API responses.
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.SYNC_TEST_BASE||'http://127.0.0.1:8796';
(async()=>{
 const [mint,market,bridge]=await Promise.all(['/api/mints?minimum=0&limit=1','/api/mints/flows?side=all&limit=25','/api/mints/bridge?minimum=0&limit=50'].map(async path=>{
  const r=await fetch(base+path);assert.equal(r.status,200);return r.json();
 }));
 assert(market.ready&&market.coverage.complete&&mint.coverage.complete);
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
 const reports=[],errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.message));
  let scenario={},holdMarket=null,releaseMarket;
  const fixture=name=>{
   const d=structuredClone(name==='mints'?mint:name==='market'?market:bridge),now=Math.floor(Date.now()/1000);
   d.now=now;
   if(name==='mints'){
    d.indexer_enabled=!scenario.paused;d.coverage.live.checked_at=now;d.coverage.live.latest_ts=now;d.coverage.live.latest_height=d.coverage.head;
    d.coverage.live.error=null;d.coverage.history.error=null;
    if(scenario.mintLag){d.coverage.covered-=scenario.mintLag;d.coverage.missing=scenario.mintLag;d.coverage.complete=false;}
    if(scenario.newHead){d.coverage.head+=scenario.newHead;d.coverage.total+=scenario.newHead;d.coverage.covered+=scenario.newHead;d.coverage.live.latest_height=d.coverage.head;}
    if(scenario.finalityGap){d.coverage.live.latest_height+=scenario.finalityGap;}
    if(scenario.staleMint)d.coverage.live.checked_at-=100;
   }else if(name==='market'){
    d.status={...d.status,checked_at:now,error:null,ok:true};d.snapshot.checked_at=now;d.snapshot.ts=now-(scenario.finalityGap?1008:0);
    if(scenario.marketLag){d.coverage.indexed_height-=scenario.marketLag;d.coverage.complete=false;d.coverage.missing=scenario.marketLag;}
    if(scenario.snapshotLag)d.snapshot.height-=scenario.snapshotLag;
    if(scenario.notReady){d.ready=false;d.snapshot=null;}
    if(scenario.rpcError)d.status.error='RPC temporarily unavailable';
    if(scenario.staleMarket)d.status.checked_at-=100;
    if(scenario.oldSnapshot)d.snapshot.checked_at-=400;
   }else if(scenario.bridgeNotReady){d.ready=false;d.snapshot=null;}
   return d;
  };
  await page.route('**/api/mints**',async route=>{
   const path=new URL(route.request().url()).pathname;
   const name=path==='/api/mints'?'mints':path==='/api/mints/flows'?'market':path==='/api/mints/bridge'?'bridge':null;
   if(!name)return route.continue();
   if(name==='market'&&holdMarket)await holdMarket;
   if(name==='market'&&scenario.offline)return route.fulfill({status:503,body:'unavailable'});
   return route.fulfill({json:fixture(name)});
  });
  const state=async expected=>{
   await page.waitForFunction(value=>document.querySelector('.connection-status').dataset.state===value,expected,{timeout:15000});
   const text=await page.locator('.connection-status').innerText();
   assert(!text.includes('NaN')&&!text.includes('undefined'));
   assert.equal(await page.locator('#status-dot').evaluate(n=>n.classList.contains('ok')),expected==='live');
   return text;
  };
  const reload=async(value,expected)=>{scenario=value;await page.goto(base+'/#trading');await page.reload();return state(expected);};
  holdMarket=new Promise(resolve=>releaseMarket=resolve);
  await page.goto(base+'/#trading');
  await page.waitForFunction(()=>document.querySelector('#coverage-detail').textContent.includes('из'));
  assert(!String(await page.locator('#live-caption').textContent()).includes('LIVE'));
  releaseMarket();holdMarket=null;await state('live');reports.push('No LIVE before both endpoints respond');
  assert((await page.locator('#snapshot-detail').textContent()).includes('Сверено до финального'));
  for(const [name,value,expected] of [
   ['Waiting for finality',{finalityGap:84},'finality'],
   ['Market backlog',{marketLag:300,snapshotLag:300},'syncing'],
   ['Mint history gap',{mintLag:100},'syncing'],
   ['Snapshot reconciliation',{snapshotLag:1},'verifying'],
   ['New final head arrived first',{newHead:32},'syncing'],
   ['RPC failure',{rpcError:true},'error'],
   ['HTTP failure',{offline:true},'error'],
   ['Collector disabled',{paused:true},'paused'],
   ['Stale mint worker',{staleMint:true},'stale'],
   ['Stale market worker',{staleMarket:true},'stale'],
   ['Expired snapshot validation',{oldSnapshot:true},'stale'],
   ['Finality does not hide index lag',{finalityGap:84,marketLag:100},'syncing'],
   ['Finality does not hide RPC errors',{finalityGap:84,rpcError:true},'error'],
   ['Finality does not hide stale workers',{finalityGap:84,staleMarket:true},'stale'],
   ['Recovery',{},'live']
  ]){
   const text=await reload(value,expected);reports.push(name);
   if(value.marketLag){
    const coverage=await page.locator('#market-coverage-detail').textContent();
    assert(coverage.includes((market.coverage.head-market.coverage.start+1-value.marketLag).toLocaleString('ru-RU')));
    await fs.mkdir('test-results',{recursive:true});
    await page.locator('.page-top').screenshot({path:'test-results/sync-status-loading.jpg',type:'jpeg',quality:85});
   }
   if(value.finalityGap){assert(text.includes('без финализации: 84 блоков'));assert(text.includes('отставание ленты 16 мин 48 с'));assert(!text.includes('LIVE'));}
   if(value.rpcError)assert(text.includes('RPC temporarily unavailable'));
  }
  // Preserve old rows while reporting that the service is rebuilding its snapshot.
  const rowCount=await page.locator('#sales-rows tr').count();
  scenario={notReady:true};
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await state('verifying');assert.equal(await page.locator('#sales-rows tr').count(),rowCount);
  reports.push('Retained table does not hide snapshot rebuilding');
  // Successful mint refresh cannot clear a failed market request.
  scenario={offline:true};await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await state('error');
  await page.getByRole('link',{name:'Мост',exact:true}).click();
  const updated=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/mints');
  await page.locator('#bridge-volume-filter button[type=submit]').click();await updated;await state('error');
  assert((await page.locator('#rpc-note').textContent()).includes('торговле'));
  reports.push('Mint refresh preserves market outage');
  scenario={newHead:32,bridgeNotReady:true};await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await state('syncing');
  reports.push('Retained bridge table still updates sync progress');
  await reload({},'live');
  // A page pinned to the previous trading snapshot cannot replace current sync metadata.
  await page.locator('#sales-next').click();
  await page.locator('#sales-pagination[aria-busy="false"]').waitFor();await state('live');
  reports.push('Pagination keeps current sync status');
  await reload({finalityGap:84},'finality');
  assert((await page.locator('.sales-table-heading').innerText()).includes('новые сделки появляются после финализации'));
  for(const [name,width] of [['desktop',1440],['mobile',390]]){
   await page.setViewportSize({width,height:900});
   await page.evaluate(()=>window.scrollTo(0,0));
   const box=await page.locator('.connection-status').boundingBox();
   assert(box.x>=0&&box.x+box.width<=width+1);
   await page.screenshot({path:'test-results/sync-status-'+name+'.jpg',type:'jpeg',quality:70});
  }
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,height:market.snapshot.height,scenarios:reports,consoleErrors:errors},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
