/* Address attribution means verified receipt initiator, not a person's identity. */
window.WgnkAddress=(()=>{
 const wallet={address:"",sort:"time_desc",request:0,data:null,loading:false,opener:null};
 document.body.insertAdjacentHTML("beforeend",'<dialog id="address-dialog" aria-labelledby="address-title"><div class="dialog-heading"><div><span class="eyebrow">WGNK / ВСЯ ИСТОРИЯ</span><h2 id="address-title">Покупки и продажи адреса</h2></div><button id="address-close" aria-label="Закрыть сделки адреса" autofocus>✕</button></div><div class="address-identity"><a id="address-link" class="mono" target="_blank" rel="noopener noreferrer"></a><button id="address-copy">Копировать</button></div><p id="address-status" class="search-result" role="status" aria-live="polite"></p><div id="address-data" hidden><section id="address-metrics" class="metrics address-metrics" aria-label="Баланс и итоги торговли адреса"></section><p class="flow-explanation">Только подтверждённый приток/отток WGNK у инициатора в двух пулах Uniswap V3. Покупки и продажи — валовой оборот, не прибыль. Переводы, чеканка и ликвидность не являются сделками. CEX, OTC и другие DEX не покрыты. Цены — USDT за 1 WGNK.</p><div class="table-container address-table" tabindex="0" aria-label="Все сделки адреса, прокручиваемый список"><table id="address-trades-table"><thead><tr><th data-sort="time">Дата и время</th><th data-sort="kind" data-default="asc">Сделка</th><th data-sort="amount" class="numeric">Объём WGNK</th><th data-sort="quote" class="numeric">Сумма USDT</th><th data-sort="price" class="numeric">Цена за 1 WGNK, USDT</th><th data-sort="pool" data-default="asc">Пул</th><th data-sort="tx" data-default="asc">Транзакция</th></tr></thead><tbody id="address-rows"></tbody></table></div><p id="address-trade-count" class="address-trade-count"></p></div><div class="address-footer"><span>Вся история одним списком · прокрутка внутри таблицы</span><button id="address-refresh">Обновить сделки</button></div></dialog>');
 const dialog=el("address-dialog");
 const PAGE_SIZE=50;
 const REFRESH_INTERVAL=30000;
 let retryAt=0,retryTimer,retryRequest;
 el("address-trade-count").insertAdjacentHTML("afterend",'<div class="pagination address-pagination" aria-label="Страницы сделок адреса"><button id="address-previous" type="button">← Назад</button><span id="address-page" aria-live="polite"></span><button id="address-next" type="button">Далее →</button></div>');
 dialog.querySelector(".address-footer span").textContent="По 50 исполнений на странице · итоги и график за всю историю";
 dialog.querySelector(".address-table").setAttribute("aria-label","Страница сделок адреса, прокручиваемый список");
 function pageButtons(){
  el("address-previous").disabled=wallet.loading||Boolean(retryRequest)||!wallet.data?.ready||wallet.data.offset<=0;
  el("address-next").disabled=wallet.loading||Boolean(retryRequest)||!wallet.data?.ready||!wallet.data.has_more;
 }
 for(const [id,direction] of [["address-previous",-1],["address-next",1]]){
  el(id).addEventListener("click",()=>{if(!wallet.loading&&wallet.data?.ready)refresh(true,true,Math.max(0,wallet.data.offset+direction*PAGE_SIZE));});
 }
 el("address-metrics").insertAdjacentHTML("afterend",'<article class="panel address-chart-panel" aria-labelledby="address-chart-title"><div class="panel-title"><div><h2 id="address-chart-title">Покупки, продажи и баланс</h2><p>По дням · вся история адреса · средняя цена взвешена по объёму</p></div><div class="market-chart-legend"><span class="trade-key buy">Покупки</span><span class="trade-key sell">Продажи</span><span class="price-key">Баланс</span></div></div><div id="address-chart" class="interactive-chart"></div><div class="chart-footer"><span>Баланс — на конец дня, последний день — на снимке.<br>Учитывает все переводы, чеканку и сжигания WGNK.</span><span>Наведите курсор или коснитесь графика</span></div></article>');
 let chartKey="";
 function renderChart(data){
  const host=el("address-chart");
  if(!data?.ready||!host.clientWidth)return;
  const key=wallet.address+":"+host.clientWidth+":"+JSON.stringify(data.address_history);
  if(chartKey===key)return;
  try{
   if(typeof window.GonkaChart?.renderAddress!=="function")throw new Error("Chart component not loaded");
   GonkaChart.renderAddress(host,data.address_history);chartKey=key;
  }catch{
   chartKey="";
   host.innerHTML='<div class="empty" role="status"><p>График не загрузился. Обновите страницу, чтобы загрузить актуальные файлы.</p><button type="button">Обновить страницу</button></div>';
   host.querySelector("button").addEventListener("click",()=>location.reload());
  }
 }
 new ResizeObserver(()=>renderChart(wallet.data)).observe(el("address-chart"));
 closeOnBackdrop(dialog);
 window.GonkaProvenance.mount(dialog);
 el("address-title").textContent="История адреса";
 el("address-refresh").textContent="Обновить";
 configureTableSort("address-trades-table",wallet.sort,sort=>refresh(true,false,0,sort));
 el("address-close").addEventListener("click",()=>dialog.close());
 dialog.addEventListener("close",()=>{
  resetRequest();
  const opener=wallet.opener?.isConnected?wallet.opener:document.querySelector('#minter-rows [data-flow-address="'+wallet.address+'"]');
  opener?.focus({preventScroll:true});
 });
 el("address-refresh").addEventListener("click",()=>window.GonkaProvenance.isActive()?window.GonkaProvenance.refresh():refresh(true));
 el("address-copy").addEventListener("click",async()=>{
  try{await navigator.clipboard.writeText(wallet.address);el("address-copy").textContent="Скопировано";}
  catch{el("address-status").textContent="Копирование недоступно. Выделите адрес вручную.";}
 });
 function render(data,resetScroll=false){
  if(!data.ready){
   el("address-status").textContent="Сверенный снимок ещё не готов. Это не означает отсутствие сделок.";
   el("address-data").hidden=true;return;
  }
  const table=dialog.querySelector(".address-table"),scrollTop=resetScroll?0:table.scrollTop,dialogTop=resetScroll?0:dialog.scrollTop;
  el("address-status").textContent=count(data.total)+" исполнений · "+count(data.summary.transactions)+" транзакций · снимок #"+count(data.snapshot.height)+" · "+date(data.snapshot.ts)+" "+clock(data.snapshot.ts)+
   (data.coverage.complete?" · история без пропусков":" · есть незагруженные блоки")+
   (data.status.error?" · свежие данные временно недоступны":data.now-data.snapshot.checked_at>180?" · обновляем данные":"");
  const s=data.summary,balance=data.address_balance;
  setLiveHTML(el("address-metrics"),'<article><span>Текущий баланс <small>WGNK</small></span><strong id="address-balance">'+(balance?esc(amount(balance.amount)):"—")+'</strong><p>'+(balance?'На проверенном блоке #'+count(balance.height)+'<br>С учётом всех переводов WGNK':'Баланс пока не подтверждён')+'</p></article><article><span>Куплено <small>WGNK</small></span><strong>'+esc(amount(s.bought))+'</strong><p>'+count(s.buys_count)+' исполнений · уплачено '+esc(amount(s.buy_quote))+' USDT<br>Средняя цена за 1 WGNK: '+esc(price(s.buy_average_price))+' USDT</p></article><article><span>Продано <small>WGNK</small></span><strong>'+esc(amount(s.sold))+'</strong><p>'+count(s.sales_count)+' исполнений · получено '+esc(amount(s.sale_quote))+' USDT<br>Средняя цена за 1 WGNK: '+esc(price(s.sale_average_price))+' USDT</p></article>');
  setTableSort("address-trades-table",data.sort);
  setLiveHTML(el("address-rows"),data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+e.kind+'">'+(e.kind==="buy"?"Покупка":"Продажа")+'</span></td><td class="numeric">'+esc(amount(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote))+'</td><td class="numeric price-value">'+esc(price(e.price))+'</td><td>Uniswap V3<small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/100)+' б.п.</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):'<tr><td colspan="7" class="empty">Подтверждённых покупок и продаж этого адреса в отслеживаемых пулах не найдено.</td></tr>');
  el("address-trade-count").textContent=count(data.total)+" исполнений за всю историю";
  el("address-page").textContent=data.total?count(data.offset+1)+"–"+count(data.offset+data.trades.length)+" из "+count(data.total):"0 исполнений";
  el("address-data").hidden=false;
  renderChart(data);
  table.scrollTop=scrollTop;dialog.scrollTop=dialogTop;
 }
 function resetRequest(){
  wallet.abort?.abort();wallet.request++;wallet.loading=false;
  clearTimeout(retryTimer);retryRequest=null;
 }
 function retryDelay(header){
  if(!header)return REFRESH_INTERVAL;
  const delay=/^\d+$/.test(header)?Number(header)*1000:Date.parse(header)-Date.now();
  return Number.isFinite(delay)?Math.max(1000,delay):REFRESH_INTERVAL;
 }
 function queueRetry(request){
  retryRequest=request;
  const delay=Math.max(0,retryAt-Date.now());
  el("address-status").textContent="Сервис временно ограничил запросы. "+
   (wallet.data?.ready?"Последние данные сохранены. ":"")+
   "Повторим автоматически через "+count(Math.ceil(delay/1000))+" с.";
  clearTimeout(retryTimer);
  retryTimer=setTimeout(resumeRetry,Math.min(delay,2147483647));
 }
 function tradingVisible(){return dialog.open&&!document.hidden&&!window.GonkaProvenance.isActive();}
 function resumeRetry(){
  if(!retryRequest||!tradingVisible()||wallet.loading)return;
  if(retryAt>Date.now()){queueRetry(retryRequest);return;}
  const {resetScroll,pageOnly,offset,sort}=retryRequest;
  clearTimeout(retryTimer);retryRequest=null;
  refresh(resetScroll,pageOnly,offset,sort,true);
 }
 async function refresh(resetScroll=false,pageOnly=false,offset=0,sort=wallet.sort,retrying=false){
  if(!dialog.open||wallet.loading||retryRequest)return;
  const request={resetScroll,pageOnly,offset,sort};
  if(retryAt>Date.now()){queueRetry(request);return;}
  wallet.sort=sort;
  const controller=wallet.abort=new AbortController(),id=++wallet.request;
  const timer=setTimeout(()=>controller.abort(),30000);wallet.loading=true;pageButtons();dialog.setAttribute("aria-busy","true");
  const previous=wallet.data;
  if(!wallet.data){el("address-status").textContent="Загружаем покупки и продажи за всю историю…";dialog.setAttribute("aria-busy","true");}
  try{
   const params=new URLSearchParams({sort,limit:PAGE_SIZE,offset});
   if(pageOnly&&previous?.ready){params.set("through",previous.snapshot.height);params.set("snapshot_hash",previous.snapshot.hash);params.set("as_of",previous.as_of??previous.now);}
   const response=await fetch("/api/mints/address/"+wallet.address+"?"+params,{signal:controller.signal});
   if(id!==wallet.request||!dialog.open)return;
   if(response.status===429){
    retryAt=Date.now()+Math.max(retryDelay(response.headers.get("Retry-After")),retrying?REFRESH_INTERVAL:0);
    queueRetry(request);return;
   }
   if(!response.ok)throw new Error(response.status===409?"Снимок истории изменился. Нажмите «Обновить».":"HTTP "+response.status);
   let data=await response.json();if(id!==wallet.request||!dialog.open)return;
   if(wallet.data?.ready&&!data.ready){el("address-status").textContent="Сервис восстанавливает снимок. Последние проверенные данные адреса сохранены; повторим автоматически.";return;}
   if(data.ready&&(data.offset!==offset||data.limit!==PAGE_SIZE||!Array.isArray(data.trades)||data.trades.length!==Math.max(0,Math.min(PAGE_SIZE,data.total-offset))))throw new Error("Страница истории загружена не полностью.");
   if(pageOnly&&data.ready){
    if(!previous?.ready||data.snapshot_height!==previous.snapshot.height||data.snapshot_hash!==previous.snapshot.hash||data.as_of!==(previous.as_of??previous.now))throw new Error("Снимок истории изменился. Нажмите «Обновить».");
    data={...previous,trades:data.trades,total:data.total,offset:data.offset,limit:data.limit,has_more:data.has_more,sort:data.sort};
   }
   render(data,resetScroll||!wallet.data);wallet.data=data;
  }catch(error){if(id===wallet.request&&dialog.open)el("address-status").textContent="Не удалось обновить сделки. "+(wallet.data?"Ниже сохранён предыдущий результат. ":"")+"Нажмите «Обновить». "+(error.name==="AbortError"?"Таймаут.":error.message);}
  finally{
   clearTimeout(timer);
   if(id===wallet.request){wallet.loading=false;dialog.setAttribute("aria-busy","false");pageButtons();}
  }
 }
 function open(address,opener,gonkaAddress=""){
  if(!/^0x[0-9a-f]{40}$/.test(address))return;
  resetRequest();
  Object.assign(wallet,{address,opener,sort:"time_desc",data:null});
  dialog.setAttribute("aria-busy","false");pageButtons();
  chartKey="";el("address-chart").replaceChildren();
  el("address-data").hidden=true;el("address-copy").textContent="Копировать";
  el("address-link").textContent=address+" ↗";el("address-link").href="https://etherscan.io/address/"+address;
  if(!dialog.open)dialog.showModal();
  window.GonkaProvenance.open(address,gonkaAddress);
  dialog.scrollTop=0;
  refresh();
 }
 function refreshVisible(){
  if(!tradingVisible()||wallet.loading)return;
  if(retryRequest){resumeRetry();return;}
  if(!wallet.data?.offset)refresh();
 }
 setInterval(refreshVisible,REFRESH_INTERVAL);
 document.addEventListener("visibilitychange",refreshVisible);
 return {open};
})();
