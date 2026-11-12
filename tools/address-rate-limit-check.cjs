// Isolated browser regression test. Only the first attempt for each phase is mocked.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');

async function main(){
 const base=process.env.TEST_URL||'http://127.0.0.1:8790';
 const address=process.env.TEST_ADDRESS||'0x9e0efada0dbfa028329389119866f6eec1f65a21';
 const browser=await chromium.launch({headless:true,channel:'chrome',chromiumSandbox:true});
 try{
  const page=await browser.newPage();
  const errors=[],requests=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.clock.install();
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   return url.origin===new URL(base).origin?route.continue():route.abort();
  });
  let retryAfter='2';
  await page.route('**/api/mints/address/'+address+'?*',route=>{
   requests.push(new URL(route.request().url()).search);
   if(retryAfter===null)return route.continue();
   const headers=retryAfter?{'Retry-After':retryAfter}:{};
   retryAfter=null;
   return route.fulfill({status:429,headers,json:{detail:'test rate limit'}});
  });
  await page.goto(base+'/#minters');
  await page.evaluate(address=>window.WgnkAddress.open(address),address);
  const status=page.locator('#address-status'),chart=page.locator('#address-chart svg');
  const refresh=page.locator('#address-refresh'),next=page.locator('#address-next');
  const rows=page.locator('#address-rows tr'),counter=page.locator('#address-page');
  const waitForStatus=text=>status.filter({hasText:text}).waitFor();
  await waitForStatus('Will retry automatically in 2 s.');
  await refresh.click();
  await page.clock.runFor(750);
  assert.equal(requests.length,1);
  await chart.waitFor();
  assert.equal(requests.length,2);
  assert.equal(requests[0],requests[1]);
  assert.equal(await rows.count(),50);

  const firstPage=await counter.innerText();
  const line=page.locator('#address-chart .address-balance-line');
  const chartBefore=await line.getAttribute('d');
  retryAfter=await page.evaluate(()=>new Date(Date.now()+4500).toUTCString());
  await next.click();
  await waitForStatus('The latest data is kept');
  assert.equal(await counter.innerText(),firstPage);
  assert.equal(await rows.count(),50);
  assert.equal(await line.getAttribute('d'),chartBefore);
  assert.equal(await next.isDisabled(),true);
  await refresh.click();
  await page.clock.runFor(1000);
  assert.equal(requests.length,3);
  await counter.filter({hasText:'51–'}).waitFor();
  assert.equal(requests.length,4);
  assert.equal(requests[2],requests[3]);
  const pinned=new URLSearchParams(requests[3]);
  assert.equal(pinned.get('offset'),'50');
  assert(pinned.has('through')&&pinned.has('snapshot_hash')&&pinned.has('as_of'));
  assert.equal(await line.getAttribute('d'),chartBefore);
  await page.clock.runFor(31000);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  assert.equal(requests.length,4,'a fixed historical page must not poll');

  retryAfter='';
  await refresh.click();
  await waitForStatus('Will retry automatically in 30 s.');
  await page.locator('#address-close').click();
  await page.clock.runFor(31000);
  assert.equal(requests.length,5,'closing the dialog must cancel a pending retry');
  assert.deepEqual(errors,[]);
  console.log('Address history: 429 recovery, pinned pagination, and cancellation passed.');
 }finally{
  await browser.close();
 }
}

main().catch(error=>{console.error(error);process.exitCode=1;});
