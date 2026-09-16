/* Local-only UI QA. Uses an isolated Chrome profile and fake Anvil funds. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
 await fs.mkdir('test-results',{recursive:true});
 const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[];
 let page;
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1050},colorScheme:'light',reducedMotion:'reduce'});
  page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',dialog=>{assert(dialog.message().includes('31337'));dialog.accept();});
  await page.goto('http://127.0.0.1:8790/#otc');
  await page.locator('#otc-wallet-address').filter({hasText:'0x'}).waitFor();
  assert.equal(page.url(),'http://127.0.0.1:8790/#otc');
  assert.equal(await page.locator('.page-top').isVisible(),false);
  const accounts=await page.locator('#otc-wallet option').evaluateAll(options=>options.map(o=>o.value));
  assert.equal(accounts.length,5);
  async function idle(){await page.waitForFunction(()=>document.querySelector('#otc-view').getAttribute('aria-busy')==='false'&&!document.querySelector('#otc-wallet').disabled);}
  async function success(){
   await page.waitForFunction(()=>['success','error'].includes(document.querySelector('#otc-status').dataset.type)&&document.querySelector('#otc-view').getAttribute('aria-busy')==='false',null,{timeout:25000});
   assert.equal(await page.locator('#otc-status').getAttribute('data-type'),'success',await page.locator('#otc-status').innerText());
  }
  async function wallet(index){await idle();await page.locator('#otc-wallet').selectOption(accounts[index]);await idle();assert.equal(await page.locator('#otc-wallet-address').textContent(),accounts[index]);}
  async function act(name,amount){await idle();if(amount!==undefined)await page.getByLabel('Сумма действия',{exact:true}).fill(String(amount));await page.getByRole('button',{name,exact:true}).click();await success();}
  async function create(symbol,w,q){
   await page.locator('#otc-seller').fill(accounts[0]);await page.locator('#otc-buyer').fill(accounts[1]);
   await page.getByLabel('Количество WGNK',{exact:true}).fill(w);
   await page.getByLabel('Встречный актив',{exact:true}).selectOption(symbol);
   await page.getByLabel('Количество встречного актива',{exact:true}).fill(q);
   await page.getByRole('button',{name:'Создать тестовый контракт ↗',exact:true}).click();await success();
  }
  await wallet(2);await create('USDC','1000','200');
  assert(await page.getByText('Этот кошелёк не участвует в сделке',{exact:true}).isVisible());
  assert.equal(await page.getByRole('button',{name:'Внести',exact:true}).count(),0);
  await wallet(0);await act('Внести','1100');await act('Вернуть сумму','25');
  assert((await page.locator('#otc-deal').innerText()).includes('1\u00a0075 WGNK'));
  await wallet(1);await act('Внести','225');assert(await page.getByRole('button',{name:'Совершить обмен ↔',exact:true}).isEnabled());
  await wallet(0);await act('Вернуть сумму','100');assert.equal(await page.getByRole('button',{name:'Совершить обмен ↔',exact:true}).isEnabled(),false);
  await act('Внести','125');
  await page.locator('#otc-view').screenshot({path:'test-results/otc-ready-light.png'});
  await wallet(1);await act('Совершить обмен ↔');
  assert.equal(await page.getByRole('button',{name:'Внести',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Совершить обмен ↔',exact:true}).count(),0);
  await act('Вернуть весь излишек');await wallet(0);await act('Вернуть весь излишек');
  assert.equal(await page.getByRole('button',{name:'Вернуть весь излишек',exact:true}).isEnabled(),false);
  await create('USDT','0.000000001','0.000001');await act('Внести','0.000000001');await wallet(1);await act('Внести','0.000001');await act('Совершить обмен ↔');
  await wallet(0);await create('ETH','1000','0.1');await act('Внести','1000');await wallet(1);await act('Внести','0.11');await act('Совершить обмен ↔');await act('Вернуть весь излишек');
  await page.locator('#otc-view').screenshot({path:'test-results/otc-completed-light.png'});
  const count=await page.locator('#otc-count').textContent();await page.reload();await idle();
  assert.equal(await page.locator('#otc-count').textContent(),count);
  assert((await page.locator('#otc-deal').innerText()).includes('Обмен выполнен'));
  await page.getByRole('link',{name:'Торговля',exact:true}).click();await page.locator('#otc-view').waitFor({state:'hidden'});
  await page.getByRole('link',{name:'OTC · локально',exact:true}).click();await page.locator('#otc-view').waitFor({state:'visible'});
  await page.locator('#theme-toggle').click();await page.locator('#otc-view').screenshot({path:'test-results/otc-dark.png'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/otc-mobile.png',fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'Mobile horizontal overflow');
  assert.equal(errors.length,0,errors.join('\n'));
  console.log(JSON.stringify({ok:true,pairs:['USDC','USDT','ETH'],tests:['create-by-third-party','role-switch','partial-withdraw','withdraw-while-ready','overfunding','swap','excess','one-shot','reload','navigation','light-dark-mobile'],contracts:count}));
 }catch(error){if(page){console.error('UI:',await page.locator('#otc-status').textContent().catch(()=>''));await page.screenshot({path:'test-results/otc-error.png',fullPage:true}).catch(()=>{});}throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
