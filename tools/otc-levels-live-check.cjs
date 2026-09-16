// Read-only smoke check of actual local helpers. All transaction paths blocked.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),results=path.join(root,'test-results');
(async()=>{
 let browser;const errors=[];let blockedWrites=0;
 try{
  const runtime=JSON.parse(fs.readFileSync(path.join(root,'otc/artifacts/WgnkOtcLevelFactory.json'),'utf8')).runtime;
  const status=[];
  for(const [port,protocol,chain]of[[8793,'gonka-otc-levels-local-v1',31337],[8794,'gonka-otc-wallet-levels-v1',31338]]){
   const base='http://127.0.0.1:'+port,headers={Origin:'http://127.0.0.1:8790'};
   const config=await (await fetch(base+'/config',{headers})).json();assert.equal(config.protocol,protocol);assert.equal(config.chainId,chain);
   const code=await (await fetch(base+'/rpc',{method:'POST',headers:{...headers,'Content-Type':'application/json','X-OTC-Session':config.session},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'eth_getCode',params:[config.factory,'latest']})})).json();assert.equal(code.result.toLowerCase(),runtime.toLowerCase());
   status.push({port,protocol,chainId:chain,factoryCodeMatches:true});
  }
  browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(/http:\/\/127\.0\.0\.1:879[34]\//,async route=>{
   const r=route.request();if(r.method()==='POST'){
    const data=JSON.parse(r.postData()||'{}');if(new URL(r.url()).pathname==='/faucet'||['eth_sendTransaction','eth_sendRawTransaction','eth_estimateGas'].includes(data.method)){blockedWrites++;await route.abort();return;}
   }await route.continue();
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  await page.waitForFunction(()=>document.getElementById('otc-network-text')?.textContent.includes('31337')&&!document.getElementById('otc-create')?.disabled);
  fs.mkdirSync(results,{recursive:true});await page.screenshot({path:path.join(results,'otc-levels-live.png')});
  assert.equal(await page.locator('#otc-wallet-dialog').isVisible(),false);await page.locator('#otc-wallet-toggle').click();await page.locator('#otc-mode-wallet').click();await page.waitForFunction(()=>document.getElementById('otc-external-panel')?.hidden===false&&!document.getElementById('otc-wallet-ack')?.disabled);
  assert.equal(await page.locator('#otc-connect-wallet').isDisabled(),true);assert.equal(await page.locator('#otc-offline').isVisible(),false);
  await page.screenshot({path:path.join(results,'otc-levels-wallet-live.png')});
  assert.deepEqual(errors,[]);assert.equal(blockedWrites,0);
  const result={ok:true,status,errors,blockedWrites,readOnly:true};fs.writeFileSync(path.join(results,'otc-levels-live-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser?.close();}
})().catch(e=>{console.error(e.stack||e.message);process.exitCode=1;});
