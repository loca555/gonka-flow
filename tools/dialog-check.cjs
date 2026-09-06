/* Isolated, read-only UI test. No user profile or wallet interaction. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.TEST_URL||'http://127.0.0.1:8790',out=path.resolve('test-results');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light',reducedMotion:'reduce'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#bridge');
  const row=page.locator('#mint-rows tr').first();await row.locator('[data-flow-address]').waitFor();
  const eth=await row.locator('[data-flow-address]').getAttribute('data-flow-address');
  const tx=await row.locator('[data-tx]').getAttribute('data-tx'),log=await row.locator('[data-tx]').getAttribute('data-log');
  const addressOpener=page.locator('#mint-rows [data-flow-address="'+eth+'"]').first();
  const detailOpener=page.locator('#mint-rows [data-tx="'+tx+'"][data-log="'+log+'"]');
  let checks=0;
  async function verifyDialog(id,opener,close){
   const dialog=page.locator('#'+id),isOpen=()=>dialog.evaluate(d=>d.open);
   const open=async()=>{await opener.click();await dialog.waitFor();if(id==='address-dialog')await page.locator('#address-data').waitFor();};
   await open();
   let box=await dialog.boundingBox();
   const exterior=()=>({x:Math.max(2,box.x/2),y:box.y+Math.min(80,box.height/2)});
   let outside=exterior(),inside={x:box.x+25,y:box.y+25};
   // Empty padding and table/header content are still inside the dialog.
   await page.mouse.click(inside.x,inside.y);assert(await isOpen());checks++;
   if(id==='address-dialog'){
    await page.locator('#address-tab-gonka').click();assert(await isOpen());
    await page.locator('#address-tab-trades').click();assert(await isOpen());
    await page.locator('#address-trades-table th[data-sort="amount"] button').click();assert(await isOpen());
   }
   box=await dialog.boundingBox();outside=exterior();inside={x:box.x+25,y:box.y+25};
   await page.mouse.move(inside.x,inside.y);await page.mouse.down();await page.mouse.move(outside.x,outside.y,{steps:4});await page.mouse.up();
   assert(await isOpen(),'inside-to-outside drag must not close');checks++;
   await page.mouse.move(outside.x,outside.y);await page.mouse.down();await page.mouse.move(inside.x,inside.y,{steps:4});await page.mouse.up();
   assert(await isOpen(),'outside-to-inside drag must not close');checks++;
   await page.mouse.click(outside.x,outside.y,{button:'right'});assert(await isOpen());checks++;
   await dialog.hover();await page.mouse.wheel(0,250);assert(await isOpen());checks++;
   await page.mouse.click(outside.x,outside.y);
   await dialog.waitFor({state:'hidden'});checks++;
   await page.waitForFunction(()=>document.querySelectorAll('dialog[open]').length===0);
   await open();await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});checks++;
   await open();await page.locator(close).click();await dialog.waitFor({state:'hidden'});checks++;
   await open();box=await dialog.boundingBox();outside=exterior();
   await page.screenshot({path:path.join(out,id+'-before-backdrop.png')});
   await page.mouse.click(outside.x,outside.y);await dialog.waitFor({state:'hidden'});checks++;
  }
  await verifyDialog('address-dialog',addressOpener,'#address-close');
  await verifyDialog('mint-dialog',detailOpener,'#close-dialog');
  await page.setViewportSize({width:390,height:844});
  await addressOpener.click();await page.locator('#address-data').waitFor();
  const box=await page.locator('#address-dialog').boundingBox();assert(box.x>0);
  await page.mouse.click(box.x/2,Math.max(10,box.y+20));
  await page.locator('#address-dialog').waitFor({state:'hidden'});checks++;
  await page.screenshot({path:path.join(out,'dialog-closed-mobile.png')});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,checks,dialogs:2,mobile:true,errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
