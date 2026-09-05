/* Isolated read-only browser QA. Never uses a user profile or wallet. */
const {chromium}=require("playwright");
const assert=require("node:assert/strict");
const fs=require("node:fs/promises");
const path=require("node:path");
(async()=>{
 const base=process.env.TEST_URL||"http://127.0.0.1:8790",out=path.resolve("test-results");
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:"chrome"});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:"light",reducedMotion:"reduce"});
  const page=await context.newPage(),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  const get=async url=>{const response=await context.request.get(base+url);assert.equal(response.status(),200,url);return response.json();};
  const mints=await get("/api/mints?minimum=0&limit=1"),bridge=await get("/api/mints/bridge?minimum=0&limit=200");
  const all=await get("/api/mints/flows?side=all&limit=200");
  assert.equal(bridge.ready,true);assert.equal(all.ready,true);
  assert.deepEqual([...mints.workers].sort(),["wgnk_market_flow","wgnk_mints_history","wgnk_mints_live"]);
  assert.equal(all.timezone,"Asia/Nicosia");
  assert.equal(BigInt(all.summary.pooled_raw)+BigInt(all.summary.outside_raw)+BigInt(all.summary.burned_raw),BigInt(all.summary.minted_raw));
  assert.equal(BigInt(all.summary.sold_raw)+BigInt(all.summary.bought_raw),BigInt(all.summary.volume_raw));
  assert(all.summary.sales_count>0&&all.summary.buys_count>0);
  assert(bridge.items.every(e=>e.finalized&&!e.native_side_checked));
  async function action(run,route,match=()=>true,settle="main"){
   const [response]=await Promise.all([page.waitForResponse(r=>{
    const u=new URL(r.url());return u.pathname===route&&r.status()===200&&match(u.searchParams);
   }),run()]);
   const data=await response.json();
   if(route.endsWith("flows")){
    await page.waitForFunction(mode=>document.getElementById(mode==="address"?"address-dialog":"sales-search").getAttribute("aria-busy")==="false",settle);
   }else if(route.endsWith("bridge")&&data.ready){
    await page.waitForFunction(d=>document.getElementById("mint-table").dataset.order===d.sort&&
     document.getElementById("page-info").textContent.startsWith(d.total?Number(d.offset+1).toLocaleString("ru-RU")+"–":"0"),data);
   }
   return data;
  }
  await page.goto(base+"/?minimum=10000&hours=24&q=0xffff#minters");
  await page.locator("#minter-rows tr").first().waitFor();
  assert.equal(await page.locator("#minters-view h1").innerText(),"Минтеры");
  assert.equal(new URL(page.url()).search,"");
  assert.equal(await page.locator(".filter-panel").count(),0);
  assert.equal(await page.locator(".scope-pills").count(),0);
  assert.equal(await page.locator(".connection-status").count(),1);
  assert.equal(await page.locator(".coverage-heading").count(),0);
  assert.equal(await page.evaluate(()=>amount("0.000000001")),"< 1");
  assert.equal(await page.evaluate(()=>amount("1234567.890001").replace(/\s/g," ")),"1 234 567");
  assert.equal(await page.evaluate(()=>count(40.17)),"40");
  assert.equal(await page.evaluate(()=>price("0.123456789123")),"123");
  assert.equal(await page.evaluate(()=>GonkaChart.exact("1234567890001",6).replace(/\s/g," ")),"1 234 567");
  assert.equal(await page.evaluate(()=>clock(Date.parse("2026-01-01T21:30:00Z")/1000)),"23:30:00");
  assert.equal(await page.evaluate(()=>date(Date.parse("2026-07-01T21:30:00Z")/1000)),"02.07.2026");
  assert.equal(await page.locator("#minter-rows tr").count(),all.minters.length);
  for(const field of ["address","dates","minted","balance","sold","price","count"]){
   const th=page.locator('#minter-table th[data-sort="'+field+'"]');
   await th.locator("button").click();const first=await th.getAttribute("aria-sort");
   await th.locator("button").click();assert.notEqual(await th.getAttribute("aria-sort"),first);
  }
  await page.locator('#minter-table th[data-sort="dates"] button').click();
  const dates=await page.locator("#minter-rows .mint-dates").evaluateAll(cells=>cells.map(c=>Number(c.dataset.lastMint)));
  const asc=await page.locator('#minter-table th[data-sort="dates"]').getAttribute("aria-sort")==="ascending";
  for(let i=1;i<dates.length;i++)assert(asc?dates[i-1]<=dates[i]:dates[i-1]>=dates[i]);
  const chosen=all.minters[0],button=page.locator('#minter-rows [data-flow-address="'+chosen.address+'"]');
  assert(chosen.first_mint_ts<=chosen.last_mint_ts&&chosen.mint_count>0);
  const mintDates=await button.locator("xpath=ancestor::tr").locator(".mint-dates").innerText();
  assert(mintDates.includes(await page.evaluate(ts=>date(ts),chosen.first_mint_ts)));
  assert(mintDates.includes(await page.evaluate(ts=>date(ts),chosen.last_mint_ts)));
  await page.screenshot({path:path.join(out,"minters-updated-light.png")});
  const trades=await action(()=>button.click(),"/api/mints/flows",p=>p.get("side")==="all"&&p.get("q")===chosen.address,"address");
  assert.equal(await page.locator("#address-dialog").isVisible(),true);
  assert.equal(new URL(page.url()).hash,"#minters");
  assert(trades.trades.every(e=>e.actor===chosen.address&&e.attribution==="initiator_net"));
  assert.equal(await page.locator("#address-link").getAttribute("href"),"https://etherscan.io/address/"+chosen.address);
  assert((await page.locator("#address-metrics").innerText()).includes("Куплено"));
  assert((await page.locator("#address-metrics").innerText()).includes("Продано"));
  for(const field of ["time","kind","amount","quote","price","pool","tx"]){
   const data=await action(()=>page.locator('#address-trades-table th[data-sort="'+field+'"] button').click(),"/api/mints/flows",
    p=>p.get("q")===chosen.address&&p.get("sort").startsWith(field+"_"),"address");
   assert.equal(data.offset,0);assert.equal(data.total,trades.total);
  }
  if(trades.total>25){
   const next=await action(()=>page.locator("#address-next").click(),"/api/mints/flows",p=>p.get("q")===chosen.address&&p.get("offset")==="25","address");
   assert.equal(next.offset,25);assert.equal(next.summary.sales_count,trades.summary.sales_count);
  }
  await page.screenshot({path:path.join(out,"address-trades-light.png")});
  await page.keyboard.press("Escape");assert.equal(await page.locator("#address-dialog").isVisible(),false);
  await page.locator('[data-view="mints"]').click();
  await page.locator("#mint-rows .tx-link").first().waitFor();
  assert.equal((await get("/api/mints/bridge?minimum=0")).total,bridge.total);
  for(const field of ["time","kind","recipient","amount","tx","status"]){
   const data=await action(()=>page.locator('#mint-table th[data-sort="'+field+'"] button').click(),"/api/mints/bridge",p=>p.get("sort").startsWith(field+"_"));
   assert.equal(data.minimum,0);assert.equal(data.total,bridge.total);
  }
  await action(()=>page.locator('#mint-table th[data-sort="amount"] button').click(),"/api/mints/bridge",p=>p.get("sort")==="amount_desc");
  const small=await action(()=>page.locator('#mint-table th[data-sort="amount"] button').click(),"/api/mints/bridge",p=>p.get("sort")==="amount_asc");
  assert(BigInt(small.items[0].amount_raw)<1000n*1000000000n);
  const burnPage=await action(()=>page.locator('#mint-table th[data-sort="kind"] button').click(),"/api/mints/bridge",p=>p.get("sort")==="kind_asc");
  const burn=burnPage.items.find(e=>e.kind==="bridge_burn");assert(burn);
  await page.locator('#mint-rows [data-tx="'+burn.tx_hash+'"][data-log="'+burn.log_index+'"]').click();
  assert.match(await page.locator("#mint-detail").innerText(),/Сжигание WGNK/);
  assert.match(await page.locator("#mint-detail").innerText(),/не проверено/);
  await page.locator("#close-dialog").click();
  const csv=await context.request.get(base+"/api/mints/bridge/export.csv?minimum=0");
  assert.equal(csv.status(),200);const csvText=await csv.text();
  assert(csvText.includes("bridge_burn")&&csvText.includes("bridge_mint")&&csvText.includes(burn.amount_raw));
  assert.equal(csvText.trim().split(/\r?\n/).length-1,bridge.total);
  await page.locator(".transactions").screenshot({path:path.join(out,"bridge-feed.png")});
  await page.locator("#sales-chart svg").scrollIntoViewIfNeeded();
  assert(await page.locator("#sales-chart .market-price").count()>0);
  assert(await page.locator("#sales-chart .market-volume").count()>0);
  assert.equal(await page.locator("[data-sales-chart]").count(),0);
  await page.locator("#sales-chart svg").focus();await page.locator("#sales-chart svg").press("End");
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Цена:/);
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Объём:/);
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-light.png")});
  const q="0x30dcdc27753d626f371ae2a18c5bb2a404a9ce40";
  await page.locator("#sales-query").fill(q);
  const found=await action(()=>page.getByRole("button",{name:"Найти продажи",exact:true}).click(),"/api/mints/flows",p=>p.get("q")===q&&p.get("side")==="sell");
  assert(found.total>=4&&found.trades.every(e=>e.kind==="sell"&&e.actor===q));
  await page.locator("#sales-query").fill("");
  const buys=await action(()=>page.getByRole("button",{name:"Найти покупки",exact:true}).click(),"/api/mints/flows",p=>p.get("side")==="buy"&&!p.get("q"));
  assert(buys.total>0&&buys.trades.every(e=>e.kind==="buy"));
  const reset=await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows",p=>p.get("side")==="all"&&!p.get("q"));
  assert(reset.summary.sales_count>0&&reset.summary.buys_count>0);
  assert.equal(await page.locator("#trades-table-title").innerText(),"Торговля");
  for(const field of ["time","kind","actor","amount","quote","price","pool","tx"]){
   const data=await action(()=>page.locator('#trades-table th[data-sort="'+field+'"] button').click(),"/api/mints/flows",p=>p.get("sort").startsWith(field+"_")&&p.get("side")==="all");
   assert.equal(data.total,reset.total);
  }
  assert.doesNotMatch((await page.locator("td.numeric").allTextContents()).join(" "),/\d[.,]\d/);
  const saved=await page.locator("#flow-sold").innerText();
  await page.route("**/api/mints/flows?*",route=>route.fulfill({status:503,body:"test outage"}));
  await page.locator("#sales-reset").click();
  await page.waitForFunction(()=>document.getElementById("sales-result").textContent.startsWith("Поиск не выполнен"));
  assert.equal(await page.locator("#flow-sold").innerText(),saved);
  await page.unroute("**/api/mints/flows?*");
  await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows");
  await page.locator("#theme-toggle").click();
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-dark.png")});
  await page.setViewportSize({width:390,height:844});
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-mobile.png")});
  assert(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
  await page.locator('[data-view="minters"]').click();
  await page.screenshot({path:path.join(out,"minters-updated-mobile.png")});
  await action(()=>page.locator('#minter-rows [data-flow-address="'+chosen.address+'"]').click(),"/api/mints/flows",p=>p.get("q")===chosen.address,"address");
  assert(await page.locator("#address-dialog").evaluate(d=>d.scrollWidth<=d.clientWidth));
  await page.screenshot({path:path.join(out,"address-trades-mobile.png")});
  await page.locator("#address-close").click();
  await page.reload();await page.locator("#minter-rows tr").first().waitFor();
  assert.equal(await page.locator("#minters-view").isVisible(),true);
  assert.equal(await page.locator("html").getAttribute("data-theme"),"dark");
  for(const route of ["/api/holders","/api/overview","/api/hosts"])assert.equal((await context.request.get(base+route)).status(),410);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,mints:mints.total,bridgeEvents:bridge.total,minters:all.minters.length,
   sales:all.summary.sales_count,buys:all.summary.buys_count,addressExecutions:trades.total,sortableColumns:28,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
