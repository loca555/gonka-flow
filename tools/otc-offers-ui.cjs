// Tests the real offer contracts in a NEW disposable, network-none EVM.
// No writes to the user's existing test network, Ethereum, or Render.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {Contract}=require('../otc/node_modules/ethers');
const root=path.resolve(__dirname,'..'),resultDir=path.join(root,'test-results');
const localImport=file=>import(pathToFileURL(path.join(root,file)).href);
const done=async tx=>(await tx).wait();
(async()=>{
 let chain,service,browser;const errors=[],checks=[];
 try{
  const {startChain,fixture,ASSETS}=await localImport('otc/scripts/evm.mjs');
  const {makeServer}=await localImport('otc/scripts/server.mjs');
  chain=await startChain();const {factory}=await fixture(chain,{offers:true});
  for(let i=0;i<27;i++)await done(factory.create(true,ASSETS.USDC.address,200000));
  service=makeServer(chain,factory,{port:0,persist:false});const base='http://127.0.0.1:'+await service.listen();
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.route('http://127.0.0.1:8791/**',async route=>{
   const request=route.request(),headers={Origin:'http://127.0.0.1:8790'};
   if(request.method()==='POST'){headers['Content-Type']='application/json';headers['X-OTC-Session']=request.headers()['x-otc-session'];}
   const response=await fetch(base+new URL(request.url()).pathname,{method:request.method(),headers,...(request.method()==='POST'?{body:request.postData()}:{}),signal:AbortSignal.timeout(20000)});
   await route.fulfill({status:response.status,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:8790','access-control-allow-headers':'Content-Type, X-OTC-Session','access-control-allow-methods':'GET, POST, OPTIONS'},body:await response.text()});
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  const idle=()=>page.waitForFunction(()=>document.getElementById('otc-view')?.getAttribute('aria-busy')==='false'&&!document.getElementById('otc-wallet')?.disabled,{},{timeout:40000});
  const expectText=(id,part)=>page.waitForFunction(({id,part})=>document.getElementById(id)?.textContent.replace(/\u00a0/g,' ').includes(part),{id,part});
  await expectText('otc-count','27');await idle();
  assert.equal(await page.locator('#otc-seller,#otc-buyer,#otc-wgnk-amount,#otc-quote-amount').count(),0);
  assert.equal(await page.locator('#otc-maker-address').textContent(),chain.accounts[0]);
  checks.push('creator-address-automatic-no-fixed-volume');
  const original=await page.locator('#otc-deal h2').textContent();
  await page.locator('#otc-input').fill('765');await page.locator('#otc-older').click();await idle();
  assert.equal(await page.locator('#otc-orders [data-deal]').count(),2);
  assert.equal(await page.locator('#otc-deal h2').textContent(),original);assert.equal(await page.locator('#otc-input').inputValue(),'765');
  await page.locator('#otc-orders [data-deal]').first().click();await idle();
  assert.equal(await page.locator('#otc-input').inputValue(),'');
  const first=await factory.dealAt(0);await page.locator('#otc-open-address').fill(first);await page.locator('#otc-open-form button').click();await idle();
  assert((await page.locator('#otc-deal').textContent()).includes(first));checks.push('pagination-address-open-context-reset');
  await page.locator('#otc-price-input').fill('0.20');await page.locator('#otc-create').click();await expectText('otc-count','28');await idle();
  const offer=new Contract(await factory.dealAt(27),chain.artifacts.WgnkOtcOffer.abi,chain.signers[0]);
  await page.locator('#otc-input').fill('100000');await page.locator('[data-action="deposit"]').click();await expectText('otc-inventory','100 000 WGNK');await idle();
  assert.equal(await offer.inventory(),100000n*10n**9n);checks.push('create-sell-and-deposit');
  await page.locator('#otc-wallet').selectOption(chain.accounts[1]);await idle();
  assert.equal(await page.locator('[data-action="withdraw"],[data-action="proceeds"]').count(),0);
  await page.locator('#otc-input').fill('5000');await expectText('otc-fill-preview','1 000 USDC');
  await page.locator('[data-action="take"]').click();await expectText('otc-inventory','95 000 WGNK');await idle();
  assert.equal(await offer.proceeds(),1000n*10n**6n);assert.equal(await offer.fillCount(),1n);
  await page.locator('#otc-wallet').selectOption(chain.accounts[2]);await idle();
  assert.equal(await page.locator('#otc-input').inputValue(),'');await page.locator('#otc-input').fill('2000');
  await page.locator('[data-action="take"]').click();await expectText('otc-inventory','93 000 WGNK');await idle();
  assert.equal(await offer.fillCount(),2n);assert.equal(await offer.proceeds(),1400n*10n**6n);checks.push('two-takers-immediate-delivery');
  await page.locator('#otc-input').fill('94000');assert.equal(await page.locator('[data-action="take"]').isDisabled(),true);
  assert((await page.locator('#otc-fill-preview').textContent()).includes('Недостаточно'));checks.push('oversize-fill-disabled');
  await page.locator('#otc-input').fill('1000');await page.locator('#otc-input').blur();
  fs.mkdirSync(resultDir,{recursive:true});await page.locator('#otc-deal').screenshot({path:path.join(resultDir,'otc-offer-taker.png')});
  await page.locator('#otc-wallet').selectOption(chain.accounts[0]);await idle();
  await page.locator('#otc-revenue-input').fill('400');await page.locator('[data-action="proceeds"]').click();await expectText('otc-proceeds','1 000 USDC');await idle();
  await page.locator('[data-action="proceeds-all"]').click();await expectText('otc-proceeds','0 USDC');await idle();
  assert.equal(await offer.proceeds(),0n);assert.equal(await offer.inventory(),93000n*10n**9n);
  await page.locator('#otc-input').fill('3000');await page.locator('[data-action="withdraw"]').click();await expectText('otc-inventory','90 000 WGNK');await idle();
  await page.locator('[data-action="withdraw-all"]').click();await expectText('otc-inventory','0 WGNK');await idle();
  await page.locator('#otc-input').fill('10000');await page.locator('[data-action="deposit"]').click();await expectText('otc-inventory','10 000 WGNK');await idle();
  assert.equal(await offer.inventory(),10000n*10n**9n);checks.push('partial-full-withdrawals-proceeds-refill');
  await page.locator('#otc-deal').screenshot({path:path.join(resultDir,'otc-offer-maker.png')});
  await page.locator('#otc-side').selectOption('buy');await page.locator('#otc-quote').selectOption('ETH');
  await page.locator('#otc-price-input').fill('0.0001');await page.locator('#otc-create').click();await expectText('otc-count','29');await idle();
  const buy=new Contract(await factory.dealAt(28),chain.artifacts.WgnkOtcOffer.abi,chain.signers[0]);
  await page.locator('#otc-input').fill('1');await page.locator('[data-action="deposit"]').click();await expectText('otc-inventory','1 ETH');await idle();
  await page.locator('#otc-wallet').selectOption(chain.accounts[3]);await idle();await page.locator('#otc-input').fill('1000');
  await expectText('otc-fill-preview','0.1 ETH');await page.locator('[data-action="take"]').click();await expectText('otc-inventory','0.9 ETH');await idle();
  assert.equal(await buy.proceeds(),1000n*10n**9n);checks.push('buy-order-native-ETH');
  for(const width of [390,320]){
   await page.setViewportSize({width,height:900});await page.locator('#otc-deal').scrollIntoViewIfNeeded();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);
   if(width===390)await page.screenshot({path:path.join(resultDir,'otc-offer-mobile.png')});
  }
  checks.push('responsive-390-320');assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(resultDir,'otc-offers-ui.json'),JSON.stringify({ok:true,checks,errors,disposableEvm:true},null,2));
  console.log(JSON.stringify({ok:true,checks,errors,disposableEvm:true}));
 }finally{await browser?.close();await service?.close();await chain?.stop();}
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
