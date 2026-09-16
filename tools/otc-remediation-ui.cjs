/* Read-only UI audit: helper RPC is fully mocked in an isolated browser profile.
 * Does not connect to or change the user's Anvil, even when it is running on 8791.
 * Regression for A02/A03: complete list, stable selection and context-bound input. */
const {chromium}=require('playwright');
const {Interface,parseUnits,toBeHex,ZeroAddress}=require('../otc/node_modules/ethers');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const escrowAbi=require('../otc/artifacts/WgnkOtc.json').abi;
const factoryAbi=require('../otc/artifacts/WgnkOtcFactory.json').abi;
const accounts=[1,2,3,4,5].map(n=>'0x'+String(n).repeat(40));
const factory='0x'+String(6).repeat(40),address=i=>'0x'+(1000+i).toString(16).padStart(40,'0');
const assets={WGNK:{symbol:'WGNK',address:'0x972a7a92d92796a98801a8818bcf91f1648f2f68',decimals:9},
 USDC:{symbol:'USDC',address:'0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',decimals:6},
 USDT:{symbol:'USDT',address:'0xdac17f958d2ee523a2206206994597c13d831ec7',decimals:6},
 ETH:{symbol:'ETH',address:ZeroAddress,decimals:18}};
const ef=new Interface(escrowAbi),ff=new Interface(factoryAbi),tf=new Interface(['function balanceOf(address) view returns(uint256)','function allowance(address,address) view returns(uint256)']);
let count=100,writes=0;
function rpc(method,params){
 if(method==='eth_chainId')return '0x7a69';
 if(method==='web3_clientVersion')return 'anvil/audit-ui-mock';
 if(method==='eth_blockNumber')return '0x100';
 if(method==='eth_accounts'||method==='eth_requestAccounts')return accounts;
 if(method==='eth_getBalance')return toBeHex(parseUnits('100',18));
 if(method==='eth_call'){
  const tx=params[0],to=tx.to.toLowerCase();
  if(to===factory){const call=ff.parseTransaction(tx);return ff.encodeFunctionResult(call.name,
   call.name==='count'?[BigInt(count)]:call.name==='isDeal'?
   [BigInt(call.args[0])>=1000n&&BigInt(call.args[0])<1000n+BigInt(count)]:[address(Number(call.args[0]))]);}
  const asset=Object.values(assets).find(a=>a.address.toLowerCase()===to);
  if(asset){const call=tf.parseTransaction(tx);return tf.encodeFunctionResult(call.name,[call.name==='balanceOf'?parseUnits('1000000',asset.decimals):0n]);}
  const call=ef.parseTransaction(tx),values={seller:accounts[0],buyer:accounts[1],quoteAsset:assets.USDC.address,
   wgnkAmount:parseUnits('1000',9),quoteAmount:parseUnits('200',6),sellerDeposit:parseUnits('1000',9),buyerDeposit:0n,executed:false,ready:false};
  assert(Object.hasOwn(values,call.name),'Unexpected getter '+call.name);
  return ef.encodeFunctionResult(call.name,[values[call.name]]);
 }
 if(/send|sign|personal|wallet/i.test(method))writes++;
 throw new Error('RPC disabled in read-only UI audit: '+method);
}
(async()=>{
 const out=path.resolve('otc/audit/2026-09-07');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[];
 const result={mockedHelper:true,realTransactions:0};
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1050},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage();page.setDefaultTimeout(60000);page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:8791/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/config')return route.fulfill({json:{protocol:'gonka-otc-local-v1',chainId:31337,simulation:true,assets,accounts,factory,session:'audit-mock-not-a-real-session'}});
   try{const body=route.request().postDataJSON();return route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:rpc(body.method,body.params||[])}});}
   catch(error){return route.fulfill({status:400,json:{error:{code:-32601,message:error.message}}});}
  });
  await page.goto('http://127.0.0.1:8790/#otc');
  async function idle(){await page.waitForFunction(()=>document.querySelector('#otc-wallet')&&!document.querySelector('#otc-wallet').disabled&&document.querySelector('#otc-count').textContent);}
  await idle();assert.equal(await page.locator('#otc-count').textContent(),'100');
  assert(await page.getByText('Локальный прототип · только тестовые активы.',{exact:true}).isVisible());
  assert.equal(await page.locator('#otc-orders [data-deal]').count(),25);
  async function clickPage(id){await page.locator(id).click();await idle();}
  for(let i=0;i<3;i++)await clickPage('#otc-older');
  await page.locator('[data-deal="'+address(0)+'"]').click();
  assert.equal(await page.locator('#otc-deal h2').textContent(),'Сделка #1');
  await page.getByLabel('Сумма действия',{exact:true}).fill('123');
  for(let i=0;i<3;i++)await clickPage('#otc-newer');
  assert.equal(await page.locator('#otc-deal h2').textContent(),'Сделка #1');
  assert.equal(await page.getByLabel('Сумма действия',{exact:true}).inputValue(),'123');
  count=101;await page.getByRole('button',{name:'Обновить OTC',exact:true}).click();await idle();
  assert.equal(await page.locator('#otc-orders [data-deal]').count(),25);
  assert.equal(await page.locator('[data-deal="'+address(0)+'"]').count(),0);
  assert.equal(await page.locator('#otc-deal h2').textContent(),'Сделка #1');
  assert.equal(await page.getByLabel('Сумма действия',{exact:true}).inputValue(),'123');
  assert(await page.locator('#otc-selection-note').isVisible());
  result.oldFundedDealRemainsSelected=true;result.amountPreservedInSameContext=true;
  await page.locator('#otc-view').screenshot({path:path.join(out,'remediation-ui-selection.png')});
  await page.locator('[data-deal="'+address(100)+'" i]').click();
  assert.equal(await page.getByLabel('Сумма действия',{exact:true}).inputValue(),'');
  await page.getByLabel('Сумма действия',{exact:true}).fill('456');
  await page.locator('#otc-wallet').selectOption(accounts[1]);await idle();
  assert.equal(await page.getByLabel('Сумма действия',{exact:true}).inputValue(),'');
  await page.getByLabel('Сумма действия',{exact:true}).fill('789');
  await page.locator('#otc-open-address').fill(address(20));
  await page.locator('#otc-open-form button').click();await idle();
  assert.equal(await page.locator('#otc-deal h2').textContent(),'Сделка по адресу');
  assert.equal(await page.getByLabel('Сумма действия',{exact:true}).inputValue(),'');
  assert((await page.locator('#otc-deal > .otc-address').textContent()).toLowerCase().includes(address(20)));
  result.inputClearedOnWalletOrDealChange=true;result.openOldDealByAddress=true;
  await page.locator('#otc-open-address').fill('0x'+'f'.repeat(40));
  await page.locator('#otc-open-form button').click();await idle();
  assert((await page.locator('#otc-deal > .otc-address').textContent()).toLowerCase().includes(address(20)));
  result.unknownAddressRejected=true;
  const listed=new Set();
  for(let i=0;i<5;i++){
   for(const a of await page.locator('#otc-orders [data-deal]').evaluateAll(nodes=>nodes.map(n=>n.dataset.deal.toLowerCase())))listed.add(a);
   if(i<4)await clickPage('#otc-older');
  }
  assert.equal(listed.size,101);
  assert(listed.has(address(0)));assert(listed.has(address(100)));
  result.allDealsAccessible=listed.size;
  await page.getByRole('link',{name:'Торговля',exact:true}).click();await page.locator('#otc-view').waitFor({state:'hidden'});
  await page.getByRole('link',{name:'OTC · локально',exact:true}).click();await page.locator('#otc-view').waitFor({state:'visible'});
  await page.locator('#theme-toggle').click();await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'remediation-ui-mobile.png')});
  assert.equal(writes,0);assert.deepEqual(errors,[]);
  result.ok=true;result.navigation=true;result.mobile=true;result.errors=errors;
 }catch(error){result.ok=false;result.error=error.message;process.exitCode=1;}
 finally{await browser.close();await fs.writeFile(path.join(out,'remediation-ui-results.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));}
})().catch(error=>{console.error(error);process.exitCode=1;});
