// Disposable EVM + clean Chrome; no user wallet/profile or saved chain is used.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url'),{Contract}=require('../otc/node_modules/ethers');
const root=path.resolve(__dirname,'..'),resultDir=path.join(root,'test-results');
const localImport=file=>import(pathToFileURL(path.join(root,file)).href),done=async tx=>(await tx).wait();
(async()=>{
 let chain,service,browser,page;const errors=[],checks=[];
 try{
  const {startChain,fixture,ASSETS}=await localImport('otc/scripts/evm.mjs'),{makeServer}=await localImport('otc/scripts/server.mjs');
  chain=await startChain();const {factory,tokens}=await fixture(chain,{levels:true});
  for(const [sells,price]of [...Array.from({length:27},(_,i)=>[true,210000+i*1000]),[false,190000],[false,180000]]){
   await done(factory.create(sells,ASSETS.USDC.address,price));const level=new Contract(await factory.levelFor(sells,ASSETS.USDC.address,price),chain.artifacts.WgnkOtcLevel.abi,chain.signers[0]);
   const amount=sells?1000n*10n**9n:190n*10n**6n;await done((sells?tokens.WGNK:tokens.USDC).approve(await level.getAddress(),amount));await done(level.deposit(amount));
  }
  service=makeServer(chain,factory,{port:0,persist:false});const base='http://127.0.0.1:'+await service.listen();
  browser=await chromium.launch({headless:true,channel:'chrome',chromiumSandbox:true});page=await browser.newPage({viewport:{width:1440,height:1100}});await page.emulateMedia({reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.route('http://127.0.0.1:8793/**',async route=>{
   const request=route.request(),headers={Origin:'http://127.0.0.1:8790'};
   if(request.method()==='POST'){headers['Content-Type']='application/json';headers['X-OTC-Session']=request.headers()['x-otc-session'];}
   const response=await fetch(base+new URL(request.url()).pathname,{method:request.method(),headers,...(request.method()==='POST'?{body:request.postData()}:{}),signal:AbortSignal.timeout(20000)});
   await route.fulfill({status:response.status,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:8790','access-control-allow-headers':'Content-Type, X-OTC-Session','access-control-allow-methods':'GET, POST, OPTIONS'},body:await response.text()});
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  const idle=()=>page.waitForFunction(()=>document.getElementById('otc-view')?.getAttribute('aria-busy')==='false'&&!document.getElementById('otc-create')?.disabled,{},{timeout:40000});
  const text=(id,expected)=>page.waitForFunction(({id,expected})=>document.getElementById(id)?.textContent.replace(/[\u00a0\u202f]/g,' ').includes(expected),{id,expected},{timeout:40000});
  const row=address=>page.locator(`#otc-orders [data-own-level="${address}"]`);
  const amount=async(address,kind,expected)=>{await page.waitForFunction(({address,kind,expected})=>document.querySelector(`#otc-orders [data-own-level="${address}"] [data-position-${kind}]`)?.textContent.replace(/[\u00a0\u202f]/g,' ')===expected,{address,kind,expected},{timeout:40000});};
  const ticket=name=>page.locator(`[data-ticket="${name}"]`).click();
  const switchWallet=async side=>{if(await page.locator('#otc-liquidity-dialog').isVisible())await page.locator('#otc-liquidity-close').click();await page.locator('#otc-wallet-toggle').click();await page.locator('#otc-wallet').selectOption(chain.accounts[side]);await idle();assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);};
  const place=async(price,value,side='sell',quote='USDC')=>{await ticket('create');await page.locator('#otc-side').selectOption(side);await page.locator('#otc-quote').selectOption(quote);await page.locator('#otc-price-input').fill(price);await page.locator('#otc-place-amount').fill(value);await page.locator('#otc-create').click();await idle();assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);};
  const edit=async(address,mode,value)=>{await row(address).locator('[data-position-action="edit"]').click();await page.locator(`[data-liquidity-mode="${mode}"]`).click();await page.locator('#otc-liquidity-amount').fill(value);await page.locator('#otc-liquidity-submit').click();await idle();assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);};
  const trade=async(address,value)=>{await page.locator(`[data-book-deal="${address}"]`).click();await page.locator('#otc-take-input').fill(value);await page.locator('[data-action="take"]').click();await idle();};
  await text('otc-count','29');await idle();assert.equal(await page.locator('[data-ticket="funds"],#otc-funds-panel,[data-deal]').count(),0);
  assert.equal(await page.locator('#otc-orders th').count(),0);assert.equal(await page.locator('.otc-my-table th').count(),4);
  assert.equal(await page.locator('#otc-asks [data-book-deal]').count(),27);assert.deepEqual(await page.locator('#otc-bids strong').allTextContents(),['0.19','0.18']);
  const asks=await page.locator('#otc-asks strong').allTextContents();assert.equal(asks[0],'0.21');assert.equal(asks.at(-1),'0.236');checks.push('book-all-levels-best-prices-no-funds-tab-no-manage-buttons');
  await place('0.2','1000');assert.equal(await factory.count(),30n);assert.equal(await page.locator('#otc-transaction-steps li').count(),3);
  const address=await factory.levelFor(true,ASSETS.USDC.address,200000),level=new Contract(address,chain.artifacts.WgnkOtcLevel.abi,chain.signers[0]);
  assert.equal(await level.inventory(),1000n*10n**9n);await amount(address,'stock','1 000');checks.push('one-form-create-approve-deposit-no-extra-window');
  await switchWallet(1);await place('0.2','3000');assert.equal(await factory.count(),30n);assert.equal(await level.inventory(),4000n*10n**9n);await amount(address,'stock','3 000');checks.push('same-price-reused-by-another-provider');
  await done(factory.create(true,ASSETS.USDC.address,250000));const empty=await factory.levelFor(true,ASSETS.USDC.address,250000),count=await factory.count();await page.locator('#otc-reload').click();await idle();
  await place('0.25','250');assert.equal(await factory.count(),count);assert.equal(await factory.levelFor(true,ASSETS.USDC.address,250000),empty);await amount(empty,'stock','250');checks.push('empty-existing-level-reused-without-new-contract');
  await switchWallet(2);await trade(address,'400');assert.equal(await level.inventory(),3600n*10n**9n);assert.equal((await level.accountState(chain.accounts[0]))[1],20000000n);assert.equal((await level.accountState(chain.accounts[1]))[1],60000000n);checks.push('proportional-fill-two-providers');
  await switchWallet(0);await amount(address,'stock','900');await amount(address,'proceeds','20');
  await row(address).locator('[data-position-action="proceeds-all"]').click();await idle();await amount(address,'proceeds','0');assert.equal((await level.accountState(chain.accounts[1]))[1],60000000n);assert.equal(await level.inventory(),3600n*10n**9n);checks.push('claim-cross-withdraws-all-proceeds-only');
  await edit(address,'deposit','900');await amount(address,'stock','1 800');await edit(address,'withdraw','900');await amount(address,'stock','900');checks.push('pencil-adds-and-reduces-own-liquidity');
  await row(address).locator('[data-position-action="edit"]').click();await page.locator('[data-liquidity-mode="withdraw"]').click();await page.locator('#otc-liquidity-amount').fill('901');assert.equal(await page.locator('#otc-liquidity-submit').isDisabled(),true);await page.locator('#otc-liquidity-amount').fill('1e3');assert.equal(await page.locator('#otc-liquidity-submit').isDisabled(),true);
  await page.locator('#otc-liquidity-max').click();assert.equal(await page.locator('#otc-liquidity-amount').inputValue(),'900');
  fs.mkdirSync(resultDir,{recursive:true});await page.locator('#otc-liquidity-dialog').screenshot({path:path.join(resultDir,'otc-liquidity-edit.png')});await page.keyboard.press('Escape');assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);
  await row(address).locator('[data-position-action="edit"]').click();await page.mouse.click(2,2);assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);checks.push('amount-validation-max-and-modal-dismissal');
  await row(address).locator('[data-position-action="withdraw-all"]').click();await idle();assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);assert.equal((await level.accountState(chain.accounts[0]))[0],0n);assert.equal((await level.accountState(chain.accounts[1]))[0],2700n*10n**9n);assert.equal(await row(address).count(),0);checks.push('stock-cross-removes-all-owned-liquidity-no-amount-dialog');
  const first=await factory.dealAt(0);await row(first).locator('[data-position-action="edit"]').click();await page.locator('[data-liquidity-mode="withdraw"]').click();await page.locator('#otc-liquidity-max').click();await page.locator('#otc-liquidity-submit').click();await idle();assert.equal(await row(first).count(),0);
  await place('0.0001','1','buy','ETH');const ethAddress=await factory.levelFor(false,ASSETS.ETH.address,100000000000000n),eth=new Contract(ethAddress,chain.artifacts.WgnkOtcLevel.abi,chain.signers[0]);assert.equal(await page.locator('#otc-transaction-steps li').count(),2);
  await switchWallet(1);await place('0.0001','3','buy','ETH');assert.equal(await page.locator('#otc-transaction-steps li').count(),1);await switchWallet(2);await trade(ethAddress,'4000');assert.equal(await eth.inventory(),3600000000000000000n);
  await switchWallet(0);await amount(ethAddress,'proceeds','1 000');await row(ethAddress).locator('[data-position-action="proceeds-all"]').click();await idle();await amount(ethAddress,'proceeds','0');checks.push('ETH-new-and-existing-levels-without-approve');
  await switchWallet(2);await trade(ethAddress,'36000');await switchWallet(0);await amount(ethAddress,'stock','0');await amount(ethAddress,'proceeds','9 000');assert.equal(await row(ethAddress).locator('[data-position-action="withdraw-all"]').isDisabled(),true);
  await row(ethAddress).locator('[data-position-action="proceeds-all"]').click();await idle();assert.equal(await row(ethAddress).count(),0);checks.push('filled-order-stays-until-proceeds-claimed');
  await page.locator('#otc-book-quote').selectOption('USDC');await page.locator('.otc-orders').screenshot({path:path.join(resultDir,'otc-order-icons.png')});await ticket('create');await page.locator('#otc-side').selectOption('sell');await page.locator('#otc-quote').selectOption('USDC');await page.locator('#otc-price-input').fill('0.2');await page.locator('#otc-place-amount').fill('1000');await page.locator('.otc-exchange').screenshot({path:path.join(resultDir,'otc-place-one-form.png')});
  for(const width of [390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await row(await factory.dealAt(1)).locator('[data-position-action="edit"]').click();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));if(width===390)await page.screenshot({path:path.join(resultDir,'otc-orders-mobile.png')});await page.locator('#otc-liquidity-close').click();}checks.push('responsive-390-320');assert.deepEqual(errors,[]);
  const result={ok:true,checks,errors,disposableEvm:true};fs.writeFileSync(path.join(resultDir,'otc-levels-ui.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){fs.mkdirSync(resultDir,{recursive:true});if(page){console.log(JSON.stringify({status:await page.locator('#otc-status').textContent(),errors}));await page.screenshot({path:path.join(resultDir,'otc-level-ui-failure.png')});}throw error;}
 finally{await browser?.close();await service?.close();await chain?.stop();}
})().catch(e=>{console.error(e.stack||e.message);process.exitCode=1;});
