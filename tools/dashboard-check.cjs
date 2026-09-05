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
  async function switchView(view){
   await page.locator('[data-view="'+view+'"]').click();
   await page.locator("#"+({bridge:"mints-view",trading:"trading-view",minters:"minters-view"})[view]).waitFor({state:"visible"});
   await page.waitForFunction(name=>document.querySelector('[data-view="'+name+'"]').getAttribute("aria-current")==="page",view);
  }
  const get=async url=>{const response=await context.request.get(base+url);assert.equal(response.status(),200,url);return response.json();};
  const mints=await get("/api/mints?minimum=0&limit=1"),bridge=await get("/api/mints/bridge?minimum=0&limit=200");
  const all=await get("/api/mints/flows?side=all&limit=200");
  assert.equal(bridge.ready,true);assert.equal(all.ready,true);
  assert.deepEqual([...mints.workers].sort(),["wgnk_market_flow","wgnk_mints_history","wgnk_mints_live"]);
  assert.equal(all.timezone,"Asia/Nicosia");
  assert.equal(BigInt(all.summary.pooled_raw)+BigInt(all.summary.outside_raw)+BigInt(all.summary.burned_raw),BigInt(all.summary.minted_raw));
  assert.equal(BigInt(all.summary.sold_raw)+BigInt(all.summary.bought_raw),BigInt(all.summary.volume_raw));
  assert(all.summary.sales_count>0&&all.summary.buys_count>0);
  for(const key of ["sold_raw","bought_raw","sale_quote_raw","buy_quote_raw"]){
   assert.equal(all.daily.reduce((sum,d)=>sum+BigInt(d[key]),0n),BigInt(all.summary[key]));
  }
  for(const d of all.daily){
   assert.equal(BigInt(d.raw),BigInt(d.sold_raw)+BigInt(d.bought_raw));
   assert.equal(BigInt(d.quote_raw),BigInt(d.sale_quote_raw)+BigInt(d.buy_quote_raw));
   assert.equal(d.events,d.buys_count+d.sales_count);
  }
  assert(bridge.items.every(e=>e.finalized&&!e.native_side_checked));
  async function action(run,route,match=()=>true,settle="main"){
   const [response]=await Promise.all([page.waitForResponse(r=>{
    const u=new URL(r.url());return u.pathname===route&&r.status()===200&&match(u.searchParams);
   }),run()]);
   const data=await response.json();
   if(route.endsWith("flows")||route.includes("/address/")){
    await page.waitForFunction(mode=>document.getElementById(mode==="address"?"address-dialog":"sales-search").getAttribute("aria-busy")==="false",settle);
   }else if(route.endsWith("bridge")&&data.ready){
    await page.waitForFunction(d=>document.getElementById("mint-table").dataset.order===d.sort&&
     document.getElementById("page-info").textContent.startsWith(d.total?Number(d.offset+1).toLocaleString("ru-RU")+"–":"0"),data);
   }
   return data;
  }
  await page.goto(base+"/");
  await page.locator("#sales-rows .tx-link").first().waitFor();
  assert.equal(new URL(page.url()).hash,"#trading");
  assert.equal(await page.locator("#trading-view").isVisible(),true);
  assert.equal(await page.locator("#mints-view").isVisible(),false);
  assert.equal(await page.locator("#minters-view").isVisible(),false);
  assert.equal(await page.locator("#flow-loading").count(),0);
  assert.doesNotMatch(await page.locator("#trading-view").innerText(),/ПОСЛЕ ЧЕКАНКИ|Пулы и сделки WGNK|Снимок #/);
  await page.screenshot({path:path.join(out,"trading-tab-light.png")});
  await switchView("bridge");
  assert.equal(await page.locator("#mints-view").isVisible(),true);
  assert.equal(await page.locator("#trading-view").isVisible(),false);
  assert.equal(await page.locator('[data-view="bridge"]').getAttribute("aria-current"),"page");
  await page.locator("#mint-chart svg").waitFor();
  await page.screenshot({path:path.join(out,"bridge-tab-light.png")});
  await page.goto(base+"/#mints");
  await page.waitForURL("**/#bridge");
  assert.equal(await page.locator("#mints-view").isVisible(),true);
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
  const addressRoute="/api/mints/address/"+chosen.address;
  const trades=await action(()=>button.click(),addressRoute,()=>true,"address");
  assert.equal(await page.locator("#address-dialog").isVisible(),true);
  assert.equal(new URL(page.url()).hash,"#minters");
  assert(trades.trades.every(e=>e.actor===chosen.address&&e.attribution==="initiator_net"));
  assert.equal(await page.locator("#address-link").getAttribute("href"),"https://etherscan.io/address/"+chosen.address);
  assert((await page.locator("#address-metrics").innerText()).includes("Куплено"));
  assert((await page.locator("#address-metrics").innerText()).includes("Продано"));
  assert.equal(await page.locator("#address-balance").innerText(),await page.evaluate(value=>amount(value),trades.address_balance.amount));
  assert.equal(trades.address_balance.height,trades.snapshot.height);
  assert.equal(trades.trades.length,trades.total);assert.equal(trades.has_more,false);
  assert.equal(await page.locator("#address-rows tr").count(),trades.total);
  assert.equal(await page.locator("#address-prev,#address-next,#address-page").count(),0);
  for(const field of ["time","kind","amount","quote","price","pool","tx"]){
   const data=await action(()=>page.locator('#address-trades-table th[data-sort="'+field+'"] button').click(),addressRoute,
    p=>p.get("sort").startsWith(field+"_"),"address");
   assert.equal(data.offset,0);assert.equal(data.total,trades.total);
   assert.equal(data.trades.length,data.total);
  }
  if(trades.total>25){
   await page.locator(".address-table").hover();await page.mouse.wheel(0,550);
   await page.waitForFunction(()=>document.querySelector(".address-table").scrollTop>100);
   const scroll=await page.locator(".address-table").evaluate(e=>e.scrollTop);
   await action(()=>page.locator("#address-refresh").click(),addressRoute,()=>true,"address");
   assert.equal(await page.locator(".address-table").evaluate(e=>e.scrollTop),scroll);
  }
  const savedBalance=await page.locator("#address-balance").innerText();
  await page.route("**"+addressRoute+"?*",route=>route.fulfill({status:503,body:"test outage"}));
  await page.locator("#address-refresh").click();
  await page.waitForFunction(()=>document.getElementById("address-status").textContent.startsWith("Не удалось"));
  assert.equal(await page.locator("#address-balance").innerText(),savedBalance);
  assert.equal(await page.locator("#address-rows tr").count(),trades.total);
  await page.unroute("**"+addressRoute+"?*");
  await action(()=>page.locator("#address-refresh").click(),addressRoute,()=>true,"address");
  await page.locator("#address-dialog").evaluate(d=>{d.scrollTop=0;});
  await page.screenshot({path:path.join(out,"address-trades-light.png")});
  await page.keyboard.press("Escape");assert.equal(await page.locator("#address-dialog").isVisible(),false);
  await switchView("bridge");
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
  await switchView("trading");
  assert.equal(await page.locator(".transactions").isVisible(),false);
  assert.equal(await page.locator("#trading-view").isVisible(),true);
  const chartData=await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows",p=>p.get("side")==="all");
  await page.locator("#sales-chart svg").scrollIntoViewIfNeeded();
  assert(await page.locator("#sales-chart .market-price").count()>0);
  assert(await page.locator("#sales-chart .market-volume").count()>0);
  assert(await page.locator("#sales-chart .market-volume.buy").count()>0);
  assert(await page.locator("#sales-chart .market-volume.sell").count()>0);
  const fills=await page.locator("#sales-chart .market-volume").evaluateAll(bars=>Object.fromEntries(bars.map(b=>[b.dataset.kind,getComputedStyle(b).fill])));
  assert.notEqual(fills.buy,fills.sell);
  assert.equal(await page.locator("[data-sales-chart]").count(),0);
  await page.locator("#sales-chart svg").focus();await page.locator("#sales-chart svg").press("End");
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Цена:/);
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Объём:/);
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Покупки/);
  assert.match(await page.locator("#sales-chart .chart-tooltip").innerText(),/Продажи/);
  async function checkBreakdowns(data){
   assert.equal(await page.locator("#flow-volume-breakdown").isVisible(),data.side==="all");
   if(data.side!=="all")return;
   for(const [id,buy,sell,format] of [["volume","bought","sold","amount"],["quote","buy_quote","sale_quote","amount"],
     ["price","buy_average_price","sale_average_price","price"]]){
    for(const [side,key] of [["buy",buy],["sell",sell]]){
     const expected=await page.evaluate(({value,format})=>format==="price"?price(value):amount(value),{value:data.summary[key],format});
     assert.equal(await page.locator('#flow-'+id+'-breakdown [data-trade-kind="'+side+'"] b').innerText(),expected);
    }
   }
  }
  await checkBreakdowns(chartData);
  const bars=await page.locator("#sales-chart .market-volume").evaluateAll(items=>items.map(b=>({kind:b.dataset.kind,date:b.dataset.date,raw:b.dataset.raw})));
  for(const day of chartData.daily){
   for(const [kind,key] of [["buy","bought_raw"],["sell","sold_raw"]]){
    assert.equal(bars.filter(b=>b.kind===kind&&b.date===day.date).reduce((sum,b)=>sum+BigInt(b.raw),0n),BigInt(day[key]));
   }
  }
  const chartFixtures=await page.evaluate(()=>{
   const host=document.createElement("div");host.id="chart-fixture";host.style.width="320px";document.body.append(host);
   try{
    const day={date:"2026-07-01",raw:"100000000000",quote_raw:"20000000",price_raw:"200000000000",
     bought_raw:"75000000000",sold_raw:"25000000000",buy_quote_raw:"15000000",sale_quote_raw:"5000000",buys_count:3,sales_count:1,events:4};
    GonkaChart.renderMarket(host,[day,{...day,date:"2026-07-03"}]);
    const one=host.querySelector(".market-volume.buy"),two=host.querySelector(".market-volume.sell");
    const stack=Math.abs(Number(two.getAttribute("y"))+Number(two.getAttribute("height"))-Number(one.getAttribute("y")))<.01;
    const ratio=Number(one.getAttribute("height"))/Number(two.getAttribute("height"));
    const gaps=host.querySelectorAll(".market-price").length;
    host.querySelector("svg").dispatchEvent(new KeyboardEvent("keydown",{key:"Home"}));
    host.querySelector("svg").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight"}));
    const gapTooltip=host.querySelector(".chart-tooltip").textContent;
    GonkaChart.renderMarket(host,[{...day,sold_raw:"0",raw:day.bought_raw}],{side:"buy"});
    const buyOnly=host.querySelectorAll(".market-volume.buy").length===1&&!host.querySelector(".market-volume.sell");
    GonkaChart.renderMarket(host,[]);
    return {stack,ratio,gaps,gapTooltip,buyOnly,empty:!host.querySelector("svg")};
   }finally{host.remove();}
  });
  assert.equal(chartFixtures.stack,true);assert.equal(chartFixtures.ratio,3);
  assert.equal(chartFixtures.gaps,2);assert.match(chartFixtures.gapTooltip,/нет сделок/);
  assert.equal(chartFixtures.buyOnly,true);assert.equal(chartFixtures.empty,true);
  await page.locator(".sales-metrics").screenshot({path:path.join(out,"trade-breakdowns-light.png")});
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-light.png")});
  const q="0x30dcdc27753d626f371ae2a18c5bb2a404a9ce40";
  await page.locator("#sales-query").fill(q);
  const found=await action(()=>page.getByRole("button",{name:"Найти продажи",exact:true}).click(),"/api/mints/flows",p=>p.get("q")===q&&p.get("side")==="sell");
  assert(found.total>=4&&found.trades.every(e=>e.kind==="sell"&&e.actor===q));
  await checkBreakdowns(found);
  assert.equal(await page.locator("#sales-chart .market-volume.buy").count(),0);
  assert.equal(await page.locator('[data-chart-side="buy"]').isVisible(),false);
  await page.locator("#sales-query").fill("");
  const buys=await action(()=>page.getByRole("button",{name:"Найти покупки",exact:true}).click(),"/api/mints/flows",p=>p.get("side")==="buy"&&!p.get("q"));
  assert(buys.total>0&&buys.trades.every(e=>e.kind==="buy"));
  await checkBreakdowns(buys);
  assert.equal(await page.locator("#sales-chart .market-volume.sell").count(),0);
  assert.equal(await page.locator('[data-chart-side="sell"]').isVisible(),false);
  const filtered=await action(()=>page.locator("#sales-period").selectOption("168"),"/api/mints/flows",p=>p.get("hours")==="168");
  assert.equal(filtered.hours,168);
  const together=await action(()=>page.getByRole("button",{name:"Все сделки",exact:true}).click(),"/api/mints/flows",p=>p.get("side")==="all"&&p.get("hours")==="168");
  await checkBreakdowns(together);
  const reset=await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows",p=>p.get("side")==="all"&&!p.get("q"));
  assert(reset.summary.sales_count>0&&reset.summary.buys_count>0);
  await checkBreakdowns(reset);
  assert.equal(await page.locator("#trades-table-title").innerText(),"Торговля");
  for(const field of ["time","kind","actor","amount","quote","price","pool","tx"]){
   const data=await action(()=>page.locator('#trades-table th[data-sort="'+field+'"] button').click(),"/api/mints/flows",p=>p.get("sort").startsWith(field+"_")&&p.get("side")==="all");
   // Live collection may advance the verified snapshot between sort requests.
   if(data.snapshot.height===reset.snapshot.height)assert.equal(data.total,reset.total);
   assert.equal(data.total,data.summary.swaps);
   assert.equal(await page.locator("#sales-total").innerText(),await page.evaluate(value=>count(value),data.total));
  }
  assert.doesNotMatch((await page.locator("td.numeric").allTextContents()).join(" "),/\d[.,]\d/);
  const saved=await page.locator("#flow-sold").innerText();
  await page.route("**/api/mints/flows?*",route=>route.fulfill({status:503,body:"test outage"}));
  await page.locator("#sales-reset").click();
  await page.waitForFunction(()=>document.getElementById("sales-result").textContent.startsWith("Поиск не выполнен"));
  assert.equal(await page.locator("#trade-status").isVisible(),true);
  assert.equal(await page.locator("#flow-sold").innerText(),saved);
  await page.unroute("**/api/mints/flows?*");
  await action(()=>page.locator("#sales-reset").click(),"/api/mints/flows");
  await page.locator("#theme-toggle").click();
  await page.locator(".sales-metrics").screenshot({path:path.join(out,"trade-breakdowns-dark.png")});
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-dark.png")});
  await page.setViewportSize({width:390,height:844});
  await switchView("bridge");
  assert.equal(await page.locator("#trading-view").isVisible(),false);
  await page.screenshot({path:path.join(out,"bridge-tab-mobile.png")});
  await switchView("trading");
  assert.equal(await page.locator("#mints-view").isVisible(),false);
  await page.screenshot({path:path.join(out,"trading-tab-mobile.png")});
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-combined-mobile.png")});
  await page.locator("#sales-chart svg").focus();await page.locator("#sales-chart svg").press("End");
  assert(await page.locator("#sales-chart .chart-tooltip").evaluate(e=>e.getBoundingClientRect().right<=innerWidth));
  await page.locator(".sales-chart-panel").screenshot({path:path.join(out,"market-tooltip-mobile.png")});
  await page.locator(".sales-metrics").screenshot({path:path.join(out,"trade-breakdowns-mobile.png")});
  assert(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
  await switchView("minters");
  await page.screenshot({path:path.join(out,"minters-updated-mobile.png")});
  await action(()=>page.locator('#minter-rows [data-flow-address="'+chosen.address+'"]').click(),addressRoute,()=>true,"address");
  assert(await page.locator("#address-dialog").evaluate(d=>d.scrollWidth<=d.clientWidth));
  assert(await page.locator("#address-dialog").evaluate(d=>d.getBoundingClientRect().height<=innerHeight));
  await page.screenshot({path:path.join(out,"address-trades-mobile.png")});
  await page.locator("#address-close").click();
  await page.reload();await page.locator("#minter-rows tr").first().waitFor();
  assert.equal(await page.locator("#minters-view").isVisible(),true);
  assert.equal(await page.locator("html").getAttribute("data-theme"),"dark");
  await switchView("trading");
  await page.reload();
  await page.locator("#sales-chart svg").waitFor();
  assert.equal(await page.locator("#trading-view").isVisible(),true);
  assert.equal(await page.locator("#mints-view").isVisible(),false);
  await page.locator('[href="#method"]').click();
  await page.locator("#method").waitFor({state:"visible"});
  assert.equal(await page.locator("#method").isVisible(),true);
  assert.equal(await page.locator("#trading-view").isVisible(),false);
  for(const route of ["/api/holders","/api/overview","/api/hosts"])assert.equal((await context.request.get(base+route)).status(),410);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,mints:mints.total,bridgeEvents:bridge.total,minters:all.minters.length,
   sales:all.summary.sales_count,buys:all.summary.buys_count,addressExecutions:trades.total,sortableColumns:28,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
