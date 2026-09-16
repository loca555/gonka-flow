// Read-only smoke check of the actually running local app/helper. No transactions.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8790';
 const health=await (await fetch(origin+'/healthz')).json();assert.equal(health.ok,true);
 const config=await (await fetch('http://127.0.0.1:8791/config',{headers:{Origin:origin}})).json();
 assert.equal(config.protocol,'gonka-otc-offers-local-v1');assert.equal(config.chainId,31337);
 assert.equal(config.simulation,true);assert(!config.storageWarning);
 const saved=JSON.parse(fs.readFileSync(path.join(root,'otc/.local-offers/state.json'),'utf8'));
 assert.equal(saved.factory.toLowerCase(),config.factory.toLowerCase());
 const artifact=JSON.parse(fs.readFileSync(path.join(root,'otc/artifacts/WgnkOtcOfferFactory.json'),'utf8'));
 assert.equal(saved.factoryRuntime,artifact.runtime);
 const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[];let writes=0;
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:8791/rpc',route=>{
   const method=route.request().postDataJSON()?.method;
   if(['eth_sendTransaction','eth_estimateGas'].includes(method)){writes++;return route.abort();}
   return route.continue();
  });
  await page.goto(origin+'/#otc');
  await page.waitForFunction(()=>document.getElementById('otc-network')?.dataset.ready==='true'&&!document.getElementById('otc-wallet')?.disabled);
  assert.equal(await page.locator('#otc-offline').isVisible(),false);
  assert.equal(await page.locator('#otc-maker-address').textContent(),config.accounts[0]);
  await page.locator('#otc-price-input').fill('0.20');
  assert.equal(await page.locator('#otc-price').textContent(),'0.2 USDC');
  const count=await page.locator('#otc-count').textContent();
  await page.locator('#otc-view').screenshot({path:path.join(root,'test-results/otc-offer-live.png')});
  assert.deepEqual(errors,[]);assert.equal(writes,0);
  console.log(JSON.stringify({ok:true,health:health.ok,model:config.protocol,count,archiveMatchesBuild:true,writes,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
