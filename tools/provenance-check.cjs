/* Read-only UI QA in an isolated Chrome context, never a user profile. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs/promises');
(async()=>{
 const base=process.env.TEST_URL||'http://127.0.0.1:8790',out=path.resolve('test-results');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1050},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const get=async route=>{const r=await context.request.get(base+route);assert.equal(r.status(),200,route);return r.json();};
  const all=await get('/api/mints/provenance');assert(all.verified>0);
  const source=all.sources.find(s=>s.address==='gonka1gcrt8wraadkw5nmn03ggqd7qrt4rcy02zp4a6x')||all.sources.find(s=>s.history.exhausted);
  assert(source);const link=all.links.find(l=>l.gnk_address===source.address),native=source.address,eth=link.eth_address;
  const route='/api/mints/gonka/'+native,selector='#minter-rows [data-flow-address="'+eth+'"][data-gnk-address="'+native+'"]';
  await page.goto(base+'/#minters');await page.locator(selector).waitFor();
  await page.screenshot({path:path.join(out,'minters-native-light.png')});
  const open=async()=>{
   const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.status()===200),page.locator(selector).click()]);
   const data=await response.json();await page.waitForFunction(()=>!document.getElementById('native-history-status').textContent.startsWith('Читаем'));
   return data;
  };
  let data=await open();assert(data.total>0);
  assert(data.address_balance&&data.address_balance.address===native);
  assert.equal(data.address_balance.scope,'bank_balance');assert(data.address_balance.height>0);
  assert.equal(await page.locator('#native-balance-value').innerText(),await page.evaluate(v=>amount(v),data.address_balance.amount));
  assert.equal(await page.locator('#native-balance-value').getAttribute('data-raw'),data.address_balance.amount_raw);
  assert.match(await page.locator('#native-balance-status').innerText(),/Блок #/);
  assert.equal(await page.locator('#address-gonka-panel').isVisible(),true);
  assert.equal(await page.locator('#address-trading-panel').isVisible(),false);
  assert.equal(await page.locator('#native-incoming-rows tr').count(),data.total);
  assert.equal(await page.locator('#native-explorer').getAttribute('href'),'https://gonka.gg/address/'+native);
  assert.match(await page.locator('#native-coverage-note').innerText(),/не независимую проверку/);
  assert.equal(data.full_chain_verified,false);
  assert(data.items.every(e=>e.address===native&&e.source==='gonkalabs_address_index'));
  const sum=data.items.reduce((a,e)=>a+BigInt(e.amount_raw),0n);assert.equal(sum,BigInt(data.amount_raw));
  for(const field of ['time','sender','kind','amount','tx']){
   const th=page.locator('#native-incoming-table th[data-sort="'+field+'"]');
   await th.locator('button').click();const order=await th.getAttribute('aria-sort');
   await th.locator('button').click();assert.notEqual(await th.getAttribute('aria-sort'),order);
  }
  await page.locator('#native-incoming-table th[data-sort="amount"] button').click();
  const quantities=await page.locator('#native-incoming-rows [data-raw]').evaluateAll(es=>es.map(e=>e.dataset.raw));
  const asc=await page.locator('#native-incoming-table th[data-sort="amount"]').getAttribute('aria-sort')==='ascending';
  for(let i=1;i<quantities.length;i++)assert(asc?BigInt(quantities[i-1])<=BigInt(quantities[i]):BigInt(quantities[i-1])>=BigInt(quantities[i]));
  const table=page.locator('#native-incoming-view .native-table');
  if(data.total>15){
   await table.hover();await page.mouse.wheel(0,700);
   await page.waitForFunction(()=>document.querySelector('#native-incoming-view .native-table').scrollTop>50);
   const top=await table.evaluate(t=>t.scrollTop);
   const [reply]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.status()===200),page.locator('#address-refresh').click()]);
   data=await reply.json();await page.waitForFunction(()=>!document.getElementById('native-history-status').textContent.startsWith('Читаем'));
   assert.equal(await table.evaluate(t=>t.scrollTop),top);
  }
  await page.locator('#address-dialog').evaluate(d=>d.scrollTop=0);
  await page.screenshot({path:path.join(out,'native-incoming-light.png')});
  const savedRows=await page.locator('#native-incoming-rows tr').count();
  const savedBalance=await page.locator('#native-balance-value').innerText();
  await page.route('**'+route,r=>r.fulfill({status:503,body:'test outage'}));
  await page.locator('#address-refresh').click();
  await page.waitForFunction(()=>document.getElementById('native-history-status').textContent.startsWith('Не удалось'));
  assert.equal(await page.locator('#native-incoming-rows tr').count(),savedRows);
  assert.equal(await page.locator('#native-balance-value').innerText(),savedBalance);
  await page.unroute('**'+route);
  for(const [snapshot,expected] of [[null,'—'],[{...data.address_balance,amount_raw:'0',amount:'0'},'0']]){
   await page.route('**'+route,r=>r.fulfill({json:{...data,address_balance:snapshot,balance_status:{error:'test source outage'}}}));
   await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.status()===200),page.locator('#address-refresh').click()]);
   await page.waitForFunction(()=>!document.getElementById('native-history-status').textContent.startsWith('Читаем'));
   assert.equal(await page.locator('#native-balance-value').innerText(),expected);
   assert.match(await page.locator('#native-balance-status').innerText(),snapshot?/Сохранённый снимок/:/не означает ноль/);
   await page.unroute('**'+route);
  }
  await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.status()===200),page.locator('#address-refresh').click()]);
  await page.waitForFunction(()=>!document.getElementById('native-history-status').textContent.startsWith('Читаем'));
  await page.locator('[data-native-view="bridge"]').click();
  const links=(await get('/api/mints/provenance?address='+eth)).links.filter(l=>l.gnk_address===native);
  assert.equal(await page.locator('#native-bridge-rows tr').count(),links.length);
  for(const field of ['time','mint','amount','native','eth','request']){
   const th=page.locator('#native-bridge-table th[data-sort="'+field+'"]');
   await th.locator('button').click();const order=await th.getAttribute('aria-sort');
   await th.locator('button').click();assert.notEqual(await th.getAttribute('aria-sort'),order);
  }
  await page.screenshot({path:path.join(out,'native-bridge-light.png')});
  await page.locator('#address-tab-trades').click();await page.locator('#address-balance').waitFor();
  assert.equal(await page.locator('#address-gonka-panel').isVisible(),false);
  await page.locator('#address-tab-gonka').focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await page.locator('#address-tab-trades').getAttribute('aria-selected'),'true');
  await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#address-gonka-panel').isVisible(),true);
  await page.locator('#address-close').click();await page.locator('#theme-toggle').click();await open();
  await page.screenshot({path:path.join(out,'native-incoming-dark.png')});
  await page.setViewportSize({width:390,height:844});
  assert(await page.locator('#address-dialog').evaluate(d=>d.scrollWidth<=d.clientWidth));
  assert(await page.locator('#address-dialog').evaluate(d=>d.getBoundingClientRect().height<=innerHeight));
  await page.screenshot({path:path.join(out,'native-incoming-mobile.png')});
  assert.doesNotMatch((await page.locator('#native-incoming-rows .numeric').allTextContents()).join(' '),/\d[.,]\d/);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#address-dialog').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,verified:all.verified,mints:all.total,nativeAddresses:all.sources.length,incoming:data.total,linkedMints:links.length,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
