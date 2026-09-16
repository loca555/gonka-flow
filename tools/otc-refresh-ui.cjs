// Disposable EVM + clean Chrome. No user profiles, keys or local wallet state.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url'),{Contract}=require('../otc/node_modules/ethers');
const root=path.resolve(__dirname,'..'),out=path.join(root,'test-results');
const localImport=file=>import(pathToFileURL(path.join(root,file)).href),done=async tx=>(await tx).wait();
(async()=>{
 let chain,service,browser,page,release;let holdNext=false,held=false;const checks=[],errors=[];
 try{
  const {startChain,fixture,ASSETS}=await localImport('otc/scripts/evm.mjs');
  const {makeServer}=await localImport('otc/scripts/server.mjs');
  chain=await startChain();const {factory,tokens}=await fixture(chain,{levels:true});
  async function levelAt(price,owner=0){
   await done(factory.connect(chain.signers[owner]).create(true,ASSETS.USDC.address,price));
   const address=await factory.levelFor(true,ASSETS.USDC.address,price),level=new Contract(address,chain.artifacts.WgnkOtcLevel.abi,chain.signers[owner]);
   await done(tokens.WGNK.connect(chain.signers[owner]).approve(address,1000n*10n**9n));await done(level.deposit(1000n*10n**9n));return level;
  }
  for(let i=0;i<18;i++)await levelAt(210000+i*1000);
  const first=await factory.dealAt(0),level=new Contract(first,chain.artifacts.WgnkOtcLevel.abi,chain.signers[1]);
  service=makeServer(chain,factory,{port:0,persist:false});const base='http://127.0.0.1:'+await service.listen();
  browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1440,height:1050}});
  await page.emulateMedia({reducedMotion:'reduce'}); // Finish intentional scroll-to-ticket before recording the baseline.
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  await page.addInitScript(()=>{
   const interval=window.setInterval;
   window.setInterval=(fn,ms,...args)=>{if(ms===8000){window.otcTick=()=>fn(...args);return 8000;}return interval(fn,ms,...args);};
  });
  await page.route('http://127.0.0.1:8793/**',async route=>{
   const req=route.request(),headers={Origin:'http://127.0.0.1:8790'};
   if(req.method()==='POST'){
    headers['Content-Type']='application/json';headers['X-OTC-Session']=req.headers()['x-otc-session'];
    if(holdNext&&req.postDataJSON()?.method==='eth_blockNumber'){holdNext=false;held=true;await new Promise(resolve=>{release=resolve;});}
   }
   const response=await fetch(base+new URL(req.url()).pathname,{method:req.method(),headers,...(req.method()==='POST'?{body:req.postData()}:{}),signal:AbortSignal.timeout(20000)});
   await route.fulfill({status:response.status,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:8790','access-control-allow-headers':'Content-Type, X-OTC-Session','access-control-allow-methods':'GET, POST, OPTIONS'},body:await response.text()});
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  const idle=()=>page.waitForFunction(()=>document.getElementById('otc-view')?.getAttribute('aria-busy')==='false'&&!document.getElementById('otc-wallet')?.disabled,{},{timeout:40000});
  const text=(id,value)=>page.waitForFunction(({id,value})=>document.getElementById(id)?.textContent.replace(/[\u00a0\u202f]/g,' ').includes(value),{id,value},{timeout:40000});
  await text('otc-count','18');await idle();
  await page.locator(`#otc-asks [data-book-deal="${first}"]`).click();
  await page.locator('#otc-deal details summary').click();await page.locator('#otc-take-input').fill('123.456789');
  await page.evaluate(()=>{
   const input=document.getElementById('otc-take-input'),book=document.getElementById('otc-asks'),table=document.querySelector('.otc-my-table');
   input.setSelectionRange(2,8);book.scrollTop=180;table.scrollTop=200;
   window.saved={input,button:document.querySelector('[data-action=deposit]'),details:document.querySelector('#otc-deal details'),row:document.querySelector('#otc-orders tr'),bookTop:book.scrollTop,tableTop:table.scrollTop,y:scrollY,top:input.getBoundingClientRect().top};
   window.disabledMutations=0;
   const enabled=new Set([...document.querySelectorAll('#otc-view button,#otc-view input,#otc-view select')].filter(n=>!n.disabled));
   new MutationObserver(rows=>{window.disabledMutations+=rows.filter(r=>r.type==='attributes'&&r.attributeName==='disabled'&&enabled.has(r.target)).length;}).observe(document.getElementById('otc-view'),{subtree:true,attributes:true,attributeFilter:['disabled']});
  });
  async function stable(){
   const result=await page.evaluate(()=>{
    const s=window.saved,n=document.getElementById('otc-take-input');
    return {input:n===s.input,button:s.button===document.querySelector('[data-action=deposit]'),details:s.details===document.querySelector('#otc-deal details')&&s.details.open,row:s.row===document.querySelector('#otc-orders tr'),focus:document.activeElement===n,value:n.value,selection:[n.selectionStart,n.selectionEnd],y:Math.abs(scrollY-s.y),top:Math.abs(n.getBoundingClientRect().top-s.top),book:Math.abs(document.getElementById('otc-asks').scrollTop-s.bookTop),table:Math.abs(document.querySelector('.otc-my-table').scrollTop-s.tableTop),disabled:window.disabledMutations};
   });
   assert.deepEqual(result,{input:true,button:true,details:true,row:true,focus:true,value:'123.456789',selection:[2,8],y:0,top:0,book:0,table:0,disabled:0});
  }
  await page.evaluate(()=>window.otcTick());await stable();checks.push('unchanged-poll-no-replacement-no-disabled-flicker');
  await chain.provider.send('evm_mine',[]);await page.evaluate(()=>window.otcTick());await stable();checks.push('new-block-retains-input-selection-focus-details-and-scroll');
  await done(tokens.USDC.connect(chain.signers[1]).approve(first,2100000n));await done(level.take(10n*10n**9n,2100000n));
  await page.evaluate(()=>window.otcTick());await text('otc-inventory','990 WGNK');await stable();checks.push('external-fill-updates-values-without-layout-jump');
  await page.evaluate(()=>{
   const book=document.getElementById('otc-asks'),top=book.getBoundingClientRect().top;
   const row=[...book.querySelectorAll('[data-book-deal]')].find(n=>n.getBoundingClientRect().bottom>top);
   window.anchor={row,y:row.getBoundingClientRect().top};
  });
  await levelAt(200000,1);await page.evaluate(()=>window.otcTick());
  assert.equal(await page.locator('#otc-asks [data-book-deal]').count(),19);
  assert(await page.evaluate(()=>Math.abs(window.anchor.row.getBoundingClientRect().top-window.anchor.y)<=1),'visible row remains at the same pixel (scrollTop rounds fractional row heights)');
  assert.equal(await page.locator('#otc-take-input').inputValue(),'123.456789');checks.push('new-better-level-preserves-visible-book-row');
  await page.locator(`#otc-orders [data-own-level="${first}"] [data-position-action="edit"]`).click();await page.locator('[data-liquidity-mode="withdraw"]').click();await page.locator('#otc-liquidity-amount').fill('123.456789');
  await page.evaluate(()=>{window.savedModal={input:document.getElementById('otc-liquidity-amount'),dialog:document.getElementById('otc-liquidity-dialog'),top:document.getElementById('otc-liquidity-dialog').getBoundingClientRect().top};window.savedModal.input.setSelectionRange(2,8);});
  await chain.provider.send('evm_mine',[]);await page.evaluate(()=>window.otcTick());
  assert.deepEqual(await page.evaluate(()=>({node:window.savedModal.input===document.getElementById('otc-liquidity-amount'),focus:document.activeElement===window.savedModal.input,value:window.savedModal.input.value,selection:[window.savedModal.input.selectionStart,window.savedModal.input.selectionEnd],open:window.savedModal.dialog.open,delta:window.savedModal.dialog.getBoundingClientRect().top-window.savedModal.top})),{node:true,focus:true,value:'123.456789',selection:[2,8],open:true,delta:0});
  await page.locator('#otc-liquidity-close').click();checks.push('edit-dialog-keeps-value-selection-focus-position-on-refresh');
  holdNext=true;await page.evaluate(()=>{window.pendingTick=window.otcTick();});
  for(let i=0;i<100&&!held;i++)await new Promise(r=>setTimeout(r,20));assert(held,'background request held');
  assert.equal(await page.locator('#otc-wallet-toggle').isDisabled(),false);
  await page.locator('#otc-wallet-toggle').click();await page.locator('#otc-wallet').selectOption(chain.accounts[1]);await idle();
  assert.equal(await page.locator('#otc-my-count').textContent(),'1');release();release=null;await page.evaluate(()=>window.pendingTick);
  assert.equal(await page.locator('#otc-my-count').textContent(),'1');assert.equal(await page.locator('#otc-wallet-address').textContent(),chain.accounts[1]);
  assert.equal(await page.locator('#otc-offline').isVisible(),false);checks.push('late-poll-cannot-overwrite-new-wallet');
  assert.deepEqual(errors,[]);fs.mkdirSync(out,{recursive:true});await page.locator('.otc-exchange').screenshot({path:path.join(out,'otc-refresh-stable.png')});
  const result={ok:true,checks,errors,disposableEvm:true};fs.writeFileSync(path.join(out,'otc-refresh-ui.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){fs.mkdirSync(out,{recursive:true});if(page)await page.screenshot({path:path.join(out,'otc-refresh-failure.png')});throw error;}
 finally{release?.();await browser?.close();await service?.close();await chain?.stop();}
})().catch(e=>{console.error(e.stack||e.message);process.exitCode=1;});
