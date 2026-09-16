// Protocol test wallet, ephemeral keys in Node memory only. NOT real extension testing.
// Signs only against a fresh network-none EVM 31338 through the restricted wallet RPC.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const {Wallet,JsonRpcProvider,Contract,formatUnits}=require('../otc/node_modules/ethers');
const root=path.resolve(__dirname,'..'),results=path.join(root,'test-results');
const localImport=file=>import(pathToFileURL(path.join(root,file)).href);
(async()=>{
 let chain,service,browser,page,rpc;const checks=[],errors=[];let sends=0;
 try{
  const {startChain,fixture,ASSETS}=await localImport('otc/scripts/evm.mjs');
  const {makeWalletServer}=await localImport('otc/scripts/wallet-server.mjs');
  chain=await startChain({chainId:31338,randomAccounts:true});const {factory}=await fixture(chain,{levels:true});
  service=makeWalletServer(chain,factory,{port:0,persist:false});const base='http://127.0.0.1:'+await service.listen();
  const config=await (await fetch(base+'/config',{headers:{Origin:'http://127.0.0.1:8790'}})).json();
  rpc=new JsonRpcProvider(config.rpcUrl,31338,{staticNetwork:true,cacheTimeout:-1,batchMaxCount:1});rpc.pollingInterval=50;
  const wallets=Array.from({length:3},()=>Wallet.createRandom().connect(rpc)),accounts=wallets.map(w=>w.address);
  browser=await chromium.launch({headless:true,channel:'chrome',chromiumSandbox:true});page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.exposeFunction('__isolatedWalletRpc',async({method,params=[]})=>{
   try{
    if(method==='eth_sendTransaction'){
     const tx=params[0];assert.equal(BigInt(tx.chainId),31338n);const signer=wallets.find(w=>w.address.toLowerCase()===tx.from.toLowerCase());assert(signer);
     const request={to:tx.to,data:tx.data,value:BigInt(tx.value||0),chainId:31338,type:2};
     if(tx.gas)request.gasLimit=BigInt(tx.gas);
     const response=await signer.sendTransaction(request);sends++;await response.wait();return {result:response.hash};
    }
    return {result:await rpc.send(method,params)};
   }catch(e){return {error:{code:e.code===4001?4001:-32000,message:String(e.shortMessage||e.message).replace(/\/wallet\/[0-9a-f]{64}/g,'/wallet/[local]').slice(0,500)}};}
  });
  await page.addInitScript(({accounts})=>{
   const listeners=new Map();let account=accounts[0],chain='0x1',reject=false,requests=0;
   const emit=(event,value)=>{for(const fn of listeners.get(event)||[])fn(value);};
   const provider={
    on(event,fn){if(!listeners.has(event))listeners.set(event,new Set());listeners.get(event).add(fn);},
    removeListener(event,fn){listeners.get(event)?.delete(fn);},
    async request({method,params=[]}){
     if(method==='eth_requestAccounts'){requests++;return [account];}if(method==='eth_accounts')return [account];if(method==='eth_chainId')return chain;
     if(method==='wallet_addEthereumChain'){if(params[0].chainId!=='0x7a6a')throw Error('wrong test chain');return null;}
     if(method==='wallet_switchEthereumChain'){chain=params[0].chainId;emit('chainChanged',chain);return null;}
     if(method==='eth_sendTransaction'&&reject){reject=false;throw Object.assign(Error('Rejected by test user'),{code:4001});}
     const r=await window.__isolatedWalletRpc({method,params});if(r.error)throw Object.assign(Error(r.error.message),{code:r.error.code});return r.result;
    }
   };
   window.addEventListener('eip6963:requestProvider',()=>window.dispatchEvent(new CustomEvent('eip6963:announceProvider',{detail:{provider,info:{name:'Isolated test wallet',uuid:'test-only',rdns:'local.test'}}})));
   window.__testWallet={account(i){account=accounts[i];emit('accountsChanged',[account]);},chain(value){chain=value;emit('chainChanged',value);},reject(){reject=true;},requests:()=>requests};
  },{accounts});
  const headers={'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:8790','access-control-allow-headers':'Content-Type, X-OTC-Session','access-control-allow-methods':'GET, POST, OPTIONS'};
  await page.route('http://127.0.0.1:8793/**',r=>r.fulfill({status:503,headers,body:'{"error":"simulation not started in isolated wallet test"}'}));
  await page.route('http://127.0.0.1:8794/**',async route=>{
   const r=route.request(),h={Origin:'http://127.0.0.1:8790'};if(r.method()==='POST'){h['Content-Type']='application/json';h['X-OTC-Session']=r.headers()['x-otc-session'];}
   const response=await fetch(base+new URL(r.url()).pathname,{method:r.method(),headers:h,...(r.method()==='POST'?{body:r.postData()}:{}),signal:AbortSignal.timeout(20000)});
   await route.fulfill({status:response.status,headers,body:await response.text()});
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  const enabled=id=>page.waitForFunction(id=>{const n=document.getElementById(id);return n&&!n.disabled;},id,{timeout:30000});
  const idle=()=>page.waitForFunction(()=>document.getElementById('otc-view')?.getAttribute('aria-busy')==='false'&&!document.getElementById('otc-create')?.disabled,{},{timeout:30000});
  const text=(id,part)=>page.waitForFunction(({id,part})=>document.getElementById(id)?.textContent.replace(/[\u00a0\u202f]/g,' ').includes(part),{id,part});
  const preview=title=>text('otc-preview-title',title).then(()=>page.locator('#otc-preview-dialog').waitFor({state:'visible'}));
  const accept=()=>page.locator('#otc-preview-confirm').click();
  const menu=async()=>{if(!await page.locator('#otc-wallet-dialog').isVisible())await page.locator('#otc-wallet-toggle').click();};
  const ticket=name=>page.locator(`[data-ticket="${name}"]`).click();
  const connect=async()=>{await menu();await enabled('otc-connect-wallet');await page.locator('#otc-connect-wallet').click();await idle();assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);};
  const change=async n=>{await page.evaluate(n=>window.__testWallet.account(n),n);await connect();};
  const faucet=async()=>{await menu();await enabled('otc-faucet');await page.locator('#otc-faucet').click();await enabled('otc-create');assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);};
  await enabled('otc-wallet-toggle');assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);await menu();await page.keyboard.press('Escape');assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);await menu();await page.mouse.click(3,3);assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);await menu();
  await enabled('otc-mode-wallet');await page.locator('#otc-mode-wallet').click();await enabled('otc-wallet-ack');
  assert.equal(await page.evaluate(()=>window.__testWallet.requests()),0);assert.equal(sends,0);await page.locator('#otc-wallet-ack').check();
  await page.locator('#otc-connect-wallet').click();await text('otc-status','Неверная сеть');assert.equal(sends,0);
  await enabled('otc-add-network');await page.locator('#otc-add-network').click();await connect();await faucet();assert.equal(sends,0);
  checks.push('discovery-no-auto-connect-mainnet-blocked-test-faucet');
  assert((await page.locator('#otc-wallet-toggle').textContent()).includes(accounts[0].slice(0,8)));await ticket('create');
  await page.locator('#otc-price-input').fill('0.2');await page.locator('#otc-place-amount').fill('1000');await page.locator('#otc-create').click();await preview('Создать общий');
  assert((await page.locator('#otc-preview-body').textContent()).includes(accounts[0]));assert((await page.locator('#otc-preview-body').textContent()).includes('31338'));
  await page.locator('#otc-preview-cancel').click();await idle();assert.equal(sends,0);assert.equal(await factory.count(),0n);
  await page.locator('#otc-create').click();await preview('Создать общий');await text('otc-preview-body','1 из 3');await accept();await preview('Разрешить точное списание');await text('otc-preview-body','2 из 3');await page.locator('#otc-preview-cancel').click();await idle();assert.equal(sends,1);
  const address=await factory.levelFor(true,ASSETS.USDC.address,200000),level=new Contract(address,chain.artifacts.WgnkOtcLevel.abi,chain.signers[0]);
  const row=()=>page.locator(`#otc-orders [data-own-level="${address}"]`);
  assert.equal(await level.inventory(),0n);await page.locator('#otc-create').click();await preview('Разрешить точное списание');assert.equal(await factory.count(),1n);checks.push('cancel-after-create-reuses-empty-level-without-second-create');
  await text('otc-preview-body','1 000 WGNK');await text('otc-preview-body','1 из 2');assert.equal(await page.locator('#otc-transaction-steps li').count(),2);await accept();await preview('Добавить ликвидность');await text('otc-preview-body','2 из 2');await page.locator('#otc-preview-cancel').click();await idle();
  assert.equal(await level.inventory(),0n);assert.equal(sends,2);checks.push('cancel-before-sign-and-after-exact-approve');
  await page.locator('#otc-place-amount').fill('500');await page.locator('#otc-create').click();await preview('Отозвать предыдущее разрешение');await text('otc-preview-body','1 из 3');assert.equal(await page.locator('#otc-transaction-steps li').count(),3);
  await page.locator('#otc-preview-cancel').click();await idle();assert.equal(sends,2);assert.equal(await level.inventory(),0n);await page.locator('#otc-place-amount').fill('1000');checks.push('reset-approve-deposit-three-stages-explicit-no-send-on-cancel');
  await page.locator('#otc-create').click();await preview('Добавить ликвидность');await text('otc-preview-body','1 из 1');assert.equal(await page.locator('#otc-transaction-steps li').count(),1);await accept();await text('otc-inventory','1 000 WGNK');await idle();assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);
  await change(1);await faucet();await ticket('create');await page.locator('#otc-place-amount').fill('3000');await page.locator('#otc-create').click();
  await preview('Разрешить точное списание');await accept();await preview('Добавить ликвидность');fs.mkdirSync(results,{recursive:true});await page.screenshot({path:path.join(results,'otc-liquidity-stage-two.png')});await accept();await text('otc-inventory','4 000 WGNK');await idle();
  assert.equal((await level.accountState(accounts[1]))[0],3000n*10n**9n);checks.push('two-signed-providers-in-one-price-level');
  await change(2);await faucet();await ticket('trade');await page.locator('#otc-take-input').fill('400');await page.locator('[data-action="take"]').click();
  await preview('Разрешить точное списание');await text('otc-preview-body','80 USDC');await accept();await preview('Выполнить обмен');
  const before=sends;await page.evaluate(()=>window.__testWallet.chain('0x1'));await page.locator('#otc-preview-dialog').waitFor({state:'hidden'});
  assert.equal(sends,before);assert.equal(await level.inventory(),4000n*10n**9n);assert.equal(await page.locator('#otc-content').isVisible(),false);
  await page.evaluate(()=>window.__testWallet.chain('0x7a6a'));await connect();assert.equal(await page.locator('#otc-take-input').inputValue(),'');
  await page.locator('#otc-take-input').fill('400');await page.locator('[data-action="take"]').click();await preview('Выполнить обмен');await accept();await text('otc-inventory','3 600 WGNK');await idle();
  assert.equal((await level.accountState(accounts[0]))[1],20000000n);assert.equal((await level.accountState(accounts[1]))[1],60000000n);
  checks.push('network-change-cancels-next-step-exact-partial-fill');
  await change(0);await page.evaluate(()=>window.__testWallet.reject());await row().locator('[data-position-action="proceeds-all"]').click();await preview('Получить свою выручку');await accept();await idle();
  assert.equal((await level.accountState(accounts[0]))[1],20000000n);await text('otc-status','отклонено');
  await row().locator('[data-position-action="proceeds-all"]').click();await preview('Получить свою выручку');await accept();await idle();assert.equal(await row().locator('[data-position-proceeds]').textContent(),'0');
  assert.equal((await level.accountState(accounts[1]))[1],60000000n);checks.push('wallet-rejection-and-owner-only-proceeds');
  await row().locator('[data-position-action="edit"]').click();await page.locator('[data-liquidity-mode="withdraw"]').click();await page.locator('#otc-liquidity-amount').fill('100');await page.locator('#otc-liquidity-submit').click();await preview('Удалить ликвидность');await text('otc-preview-body','100 WGNK');await accept();await idle();const remaining=(await level.accountState(accounts[0]))[0];assert(remaining<=800n*10n**9n&&800n*10n**9n-remaining<=1n);assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);
  // Proportional share burning rounds by at most one WGNK base unit here.
  await row().locator('[data-position-action="withdraw-all"]').click();await preview('Удалить всю ликвидность');await text('otc-preview-body',formatUnits(remaining,9)+' WGNK');await accept();await idle();assert.equal(await row().count(),0);const otherStock=(await level.accountState(accounts[1]))[0];assert(otherStock>=2700n*10n**9n&&otherStock-2700n*10n**9n<=1n);checks.push('signed-pencil-partial-withdraw-and-cross-withdraw-all');
  await change(1);await row().locator('[data-position-action="edit"]').click();await page.locator('[data-liquidity-mode="withdraw"]').click();await page.locator('#otc-liquidity-amount').fill('100');await page.locator('#otc-liquidity-submit').click();await preview('Удалить ликвидность');const beforeAccount=sends;
  fs.mkdirSync(results,{recursive:true});await page.screenshot({path:path.join(results,'otc-wallet-preview.png')});
  for(const width of [390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  await page.screenshot({path:path.join(results,'otc-wallet-mobile.png')});
  await page.evaluate(()=>window.__testWallet.account(2));await page.locator('#otc-preview-dialog').waitFor({state:'hidden'});assert.equal(await page.locator('#otc-liquidity-dialog').isVisible(),false);assert.equal(sends,beforeAccount);
  assert.equal((await level.accountState(accounts[1]))[1],60000000n);await connect();await menu();await page.locator('#otc-disconnect-wallet').click();assert.equal(await page.locator('#otc-content').isVisible(),false);
  assert.equal(await page.locator('#otc-wallet-toggle').textContent(),'Подключить кошелёк');await page.keyboard.press('Escape');
  await page.reload();await enabled('otc-wallet-toggle');await text('otc-status','Выберите отдельный');assert.equal(await page.evaluate(()=>window.__testWallet.requests()),0);assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);assert.equal(await page.locator('#otc-wallet-toggle').textContent(),'Подключить кошелёк');checks.push('compact-wallet-menu-dismissal-mode-remembered-no-auto-connect');
  checks.push('account-change-cancels-preview-responsive-disconnect');assert.deepEqual(errors,[]);
  const result={ok:true,checks,errors,signedTestTransactions:sends,chainId:31338,realExtensionTested:false};fs.writeFileSync(path.join(results,'otc-level-wallet-ui.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){fs.mkdirSync(results,{recursive:true});if(page){console.log(JSON.stringify({status:await page.locator('#otc-status').textContent(),errors}));await page.screenshot({path:path.join(results,'otc-wallet-ui-failure.png')});}throw e;}
 finally{await browser?.close();rpc?.destroy();await service?.close();await chain?.stop();}
})().catch(e=>{console.error(String(e.stack||e.message).replace(/\/wallet\/[0-9a-f]{64}/g,'/wallet/[local]'));process.exitCode=1;});
