/* Isolated headless Chrome UI regression; does not connect to the user's browser. */
const {chromium}=require("playwright");
const assert=require("node:assert/strict");
const path=require("node:path");
(async()=>{
 const browser=await chromium.launch({headless:true,channel:"chrome"});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100},colorScheme:"light"});
  const base=process.env.TEST_URL||"http://127.0.0.1:8790",since="1780779600";
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.goto(base+"/#overview");
  await page.locator("#archive-period").waitFor({state:"visible",timeout:30000});
  assert.equal(await page.locator("#archive-period").textContent(),"С 07.06");
  const response=page.waitForResponse(r=>r.url().includes("/api/overview?since="+since));
  await page.getByRole("button",{name:"С 07.06",exact:true}).click();
  const overview=await (await response).json();
  assert.equal(overview.since,Number(since));
  await page.locator("#coverage-note").filter({hasText:"с 07.06.2026"}).waitFor();
  assert.equal(await page.locator("#history-progress").isVisible(),true);
  assert((await page.locator("#export-link").getAttribute("href")).includes("since="+since));
  await page.screenshot({path:path.resolve("test-results/history-light.png")});
  for(const [tab,endpoint] of [["activity","events"],["bridge","bridges"],["hosts","rankings"],["holders","holders"]]){
   const reply=page.waitForResponse(r=>r.url().includes("/api/"+endpoint+"?")&&new URL(r.url()).searchParams.get("since")===since);
   reply.catch(()=>{});
   await page.locator('button.nav-item[data-tab="'+tab+'"]').click();
   assert.equal((await reply).status(),200);
   await page.locator("#view-"+tab).waitFor({state:"visible"});
  }
  assert.equal(await page.locator("#holder-minimum").inputValue(),"10000");
  const holders=await (await page.request.get(base+"/api/holders?asset=WGNK&minimum=10000")).json();
  assert(holders.items.length);
  await page.goto(base+"/#address/"+holders.items[0].address);
  await page.locator("#profile-archive-option").filter({hasText:"07.06.2026"}).waitFor({state:"attached"});
  const addressReply=page.waitForResponse(r=>r.url().includes("/api/addresses/")&&r.url().includes("since="+since));
  await page.locator("#profile-period").selectOption("archive");
  const profile=await (await addressReply).json();
  assert.equal(profile.since,Number(since));
  assert(profile.events.items.every(e=>e.ts>=Number(since)));
  await page.getByRole("button",{name:"Все операции",exact:true}).click();
  await page.locator("#profile-kinds .selected").filter({hasText:"Все операции"}).waitFor();
  await page.getByRole("button",{name:"Светлая тема",exact:true}).click();
  await page.screenshot({path:path.resolve("test-results/history-dark.png")});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.resolve("test-results/history-mobile.png"),fullPage:false});
  const width=await page.evaluate(()=>({body:document.body.scrollWidth,viewport:innerWidth}));
  assert(width.body<=width.viewport+1,JSON.stringify(width));
  await page.getByRole("link",{name:"← К держателям",exact:true}).click();
  const archiveMobile=page.waitForResponse(r=>r.url().includes("/api/holders?")&&r.url().includes("since="+since));
  await page.getByRole("button",{name:"С 07.06",exact:true}).click();
  assert.equal((await archiveMobile).status(),200);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,period:since,archive:overview.archive,checked:["all tabs","profile","10k default","CSV link","mobile","themes","no JS errors"]}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
