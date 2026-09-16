const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[];let blockedWrites=0;
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:8791/**',route=>{
   if(new URL(route.request().url()).pathname==='/config')return route.continue();
   const body=route.request().postDataJSON();
   if(!['eth_chainId','eth_blockNumber','eth_call','eth_getBalance','eth_accounts','web3_clientVersion'].includes(body?.method)){blockedWrites++;return route.abort();}
   return route.continue();
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  await page.waitForFunction(()=>document.getElementById('otc-count')?.textContent==='7'&&!document.getElementById('otc-wallet').disabled);
  assert.equal(await page.locator('#otc-orders [data-deal]').count(),7);
  const heading=await page.locator('#otc-deal h2').textContent();assert.match(heading,/Сделка #/);
  await page.locator('#otc-deal').screenshot({path:'test-results/otc-restored-deal.png'});
  assert.equal(blockedWrites,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,liveHelper:true,restoredDeals:7,heading,blockedWrites,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
