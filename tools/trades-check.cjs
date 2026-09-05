/* Separate test browser; no user profile, wallet or on-chain writes. */
const {chromium}=require("playwright");
const assert=require("node:assert/strict");
const path=require("node:path");
(async()=>{
 const base=process.env.TEST_URL||"http://127.0.0.1:8790";
 const browser=await chromium.launch({headless:true,channel:"chrome"});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:"light"});
  const page=await context.newPage(),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  async function action(run,route,match=()=>true){
   const [response]=await Promise.all([page.waitForResponse(r=>{
    const u=new URL(r.url());return u.pathname===route&&r.status()===200&&match(u.searchParams);
   }),run()]);
   const data=await response.json();
   if(route.endsWith("flows"))await page.waitForFunction(()=>document.getElementById("sales-search").getAttribute("aria-busy")==="false");
   return data;
  }
  await page.goto(base);
  await page.locator("#sales-rows .tx-link").first().waitFor();
  const q="0x30dcdc27753d626f371ae2a18c5bb2a404a9ce40";
  await page.locator("#sales-query").fill(q);
  const found=await action(()=>page.getByRole("button",{name:"Найти продажи",exact:true}).click(),"/api/mints/flows",p=>p.get("q")===q&&p.get("side")==="sell");
  assert(found.total>=4);
  assert(found.trades.every(e=>e.actor===q&&e.kind==="sell"&&e.attribution==="initiator_net"));
  assert.match(await page.locator("#sales-result").innerText(),new RegExp("найдено "+found.total));
  assert.equal(await page.locator("#sales-rows tr").count(),found.trades.length);
  await page.locator("#sales-query").fill("");
  const buys=await action(()=>page.getByRole("button",{name:"Найти покупки",exact:true}).click(),"/api/mints/flows",p=>p.get("q")===""&&p.get("side")==="buy");
  assert(buys.total>0&&buys.trades.every(e=>e.kind==="buy"));
  assert.equal(buys.sales.length,0);assert.equal(buys.summary.sold,"0");
  assert.equal(await page.locator("#flow-volume-label").innerText(),"Куплено из пулов");
  assert.equal(await page.locator("#trades-table-title").innerText(),"Лента покупок");
  assert.doesNotMatch(await page.locator("#sales-rows").innerText(),/Отток WGNK/);
  for(const field of ["time","actor","amount","quote","price","pool","tx"]){
   for(let i=0;i<2;i++){
    const data=await action(()=>page.locator('#trades-table th[data-sort="'+field+'"] button').click(),"/api/mints/flows",p=>p.get("sort").startsWith(field+"_"));
    assert.equal(data.offset,0);
    assert.equal(await page.locator('#trades-table th[data-sort="'+field+'"]').getAttribute("aria-sort"),data.sort.endsWith("_asc")?"ascending":"descending");
    assert(data.trades.every(e=>e.kind==="buy"));
   }
  }
  await action(()=>page.locator("#sales-next").click(),"/api/mints/flows",p=>p.get("offset")==="25"&&p.get("side")==="buy");
  assert.match(await page.locator("#sales-page").innerText(),/^26/);
  await action(()=>page.getByRole("button",{name:"Найти продажи",exact:true}).click(),"/api/mints/flows",p=>p.get("side")==="sell"&&p.get("offset")==="0");
  await page.locator("#sales-query").fill("0x"+"f".repeat(40));
  const empty=await action(()=>page.getByRole("button",{name:"Найти продажи",exact:true}).click(),"/api/mints/flows",p=>p.get("q")==="0x"+"f".repeat(40));
  assert.equal(empty.total,0);assert.match(await page.locator("#sales-result").innerText(),/найдено 0/);
  await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows",p=>p.get("q")==="");
  for(const field of ["time","recipient","amount","tx","status"]){
   for(let i=0;i<2;i++){
    const data=await action(()=>page.locator('#mint-table th[data-sort="'+field+'"] button').click(),"/api/mints",p=>p.get("sort").startsWith(field+"_"));
    await page.waitForFunction(sort=>document.getElementById("mint-table").dataset.order===sort,data.sort);
    assert.equal(data.offset,0);
   }
  }
  await page.locator('[data-view="minters"]').click();
  await page.locator("#minters-view").waitFor({state:"visible"});
  assert.equal(await page.locator("#mints-view").isVisible(),false);
  assert.equal(await page.locator("#minters-view").isVisible(),true);
  const all=await (await context.request.get(base+"/api/mints/flows")).json();
  assert.equal(await page.locator("#minter-rows tr").count(),all.minters.length);
  for(const field of ["address","minted","balance","sold","price","count"]){
   const th=page.locator('#minter-table th[data-sort="'+field+'"]');
   await th.locator("button").click();
   const first=await th.getAttribute("aria-sort");
   await th.locator("button").click();
   assert.notEqual(await th.getAttribute("aria-sort"),first);
  }
  await page.locator('#minter-table th[data-sort="balance"] button').click();
  const balances=await page.locator("#minter-rows tr td:nth-child(3)").evaluateAll(cells=>cells.map(c=>c.title));
  const raw=s=>BigInt(s.split(".")[0]+(s.split(".")[1]||"").padEnd(9,"0"));
  const asc=(await page.locator('#minter-table th[data-sort="balance"]').getAttribute("aria-sort"))==="ascending";
  for(let i=1;i<balances.length;i++)assert(asc?raw(balances[i-1])<=raw(balances[i]):raw(balances[i-1])>=raw(balances[i]));
  await page.screenshot({path:path.resolve("test-results/minters-tab-light.png")});
  await page.reload();await page.locator("#minter-rows tr").first().waitFor();
  assert.equal(await page.locator("#minters-view").isVisible(),true);
  assert.doesNotMatch(await page.locator("body").innerText(),/Кипр/i);
  await page.locator("#theme-toggle").click();
  await page.screenshot({path:path.resolve("test-results/minters-tab-dark.png")});
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-view="mints"]').click();
  await page.locator("#mints-view").waitFor({state:"visible"});
  assert.equal(await page.locator("#mints-view").isVisible(),true);
  await page.locator('[data-view="minters"]').click();
  await page.locator("#minters-view").waitFor({state:"visible"});
  assert.equal(await page.locator("#minters-view").isVisible(),true);
  assert(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
  await page.screenshot({path:path.resolve("test-results/minters-tab-mobile.png")});
  await page.locator('[data-view="mints"]').click();
  await page.locator(".sales-heading").scrollIntoViewIfNeeded();
  await page.screenshot({path:path.resolve("test-results/trades-mobile.png")});
  assert(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,matchedSales:found.total,buyExecutions:buys.total,sortableColumns:18,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
