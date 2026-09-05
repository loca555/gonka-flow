/* Address attribution means verified receipt initiator, not a person's identity. */
window.WgnkAddress=(()=>{
 const wallet={address:"",offset:0,sort:"time_desc",request:0,data:null,loading:false,opener:null};
 document.body.insertAdjacentHTML("beforeend",'<dialog id="address-dialog" aria-labelledby="address-title"><div class="dialog-heading"><div><span class="eyebrow">WGNK / ВСЯ ИСТОРИЯ</span><h2 id="address-title">Покупки и продажи адреса</h2></div><button id="address-close" aria-label="Закрыть сделки адреса" autofocus>✕</button></div><div class="address-identity"><a id="address-link" class="mono" target="_blank" rel="noopener noreferrer"></a><button id="address-copy">Копировать</button></div><p id="address-status" class="search-result" role="status" aria-live="polite"></p><div id="address-data" hidden><section id="address-metrics" class="metrics address-metrics" aria-label="Итоги торговли адреса"></section><p class="flow-explanation">Только подтверждённый приток/отток WGNK у инициатора в двух пулах Uniswap V3. Покупки и продажи — валовой оборот, не прибыль. Переводы, чеканка и ликвидность не являются сделками. CEX, OTC и другие DEX не покрыты. Цены — USDT за 1 000 WGNK.</p><div class="table-container address-table"><table id="address-trades-table"><thead><tr><th data-sort="time">Дата и время</th><th data-sort="kind" data-default="asc">Сделка</th><th data-sort="amount" class="numeric">Объём WGNK</th><th data-sort="quote" class="numeric">Сумма USDT</th><th data-sort="price" class="numeric">Цена за 1 000 WGNK, USDT</th><th data-sort="pool" data-default="asc">Пул</th><th data-sort="tx" data-default="asc">Транзакция</th></tr></thead><tbody id="address-rows"></tbody></table></div><div class="pagination"><button id="address-prev">← Назад</button><span id="address-page"></span><button id="address-next">Далее →</button></div></div><div class="address-footer"><span>Без лимита по объёму · все страницы истории</span><button id="address-refresh">Обновить сделки</button></div></dialog>');
 const dialog=el("address-dialog");
 configureTableSort("address-trades-table",wallet.sort,sort=>{wallet.sort=sort;wallet.offset=0;refresh();});
 el("address-close").addEventListener("click",()=>dialog.close());
 dialog.addEventListener("close",()=>{
  wallet.abort?.abort();wallet.request++;wallet.loading=false;
  const opener=wallet.opener?.isConnected?wallet.opener:document.querySelector('#minter-rows [data-flow-address="'+wallet.address+'"]');
  opener?.focus({preventScroll:true});
 });
 el("address-prev").addEventListener("click",()=>{wallet.offset=Math.max(0,(wallet.data?.offset||0)-25);refresh();});
 el("address-next").addEventListener("click",()=>{wallet.offset=(wallet.data?.offset||0)+25;refresh();});
 el("address-refresh").addEventListener("click",()=>refresh());
 el("address-copy").addEventListener("click",async()=>{
  try{await navigator.clipboard.writeText(wallet.address);el("address-copy").textContent="Скопировано";}
  catch{el("address-status").textContent="Копирование недоступно. Выделите адрес вручную.";}
 });
 function render(data){
  if(!data.ready){
   el("address-status").textContent="Сверенный снимок ещё не готов. Это не означает отсутствие сделок.";
   el("address-data").hidden=true;return;
  }
  el("address-status").textContent=count(data.total)+" исполнений · "+count(data.summary.transactions)+" транзакций · снимок #"+count(data.snapshot.height)+" · "+date(data.snapshot.ts)+" "+clock(data.snapshot.ts)+
   (data.coverage.complete?" · история без пропусков":" · есть незагруженные блоки")+
   (data.status.error?" · свежие данные временно недоступны":"");
  const s=data.summary;
  el("address-metrics").innerHTML='<article><span>Куплено <small>WGNK</small></span><strong>'+esc(amount(s.bought))+'</strong><p>Уплачено '+esc(amount(s.buy_quote))+' USDT<br>Средняя цена за 1 000 WGNK: '+esc(price(s.buy_average_price))+' USDT</p></article><article><span>Продано <small>WGNK</small></span><strong>'+esc(amount(s.sold))+'</strong><p>Получено '+esc(amount(s.sale_quote))+' USDT<br>Средняя цена за 1 000 WGNK: '+esc(price(s.sale_average_price))+' USDT</p></article><article><span>Исполнений <small>SWAP</small></span><strong>'+count(data.total)+'</strong><p>'+count(s.buys_count)+' покупок · '+count(s.sales_count)+' продаж<br>Итоги по всей истории адреса</p></article>';
  setTableSort("address-trades-table",data.sort);
  el("address-rows").innerHTML=data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+e.kind+'">'+(e.kind==="buy"?"Покупка":"Продажа")+'</span></td><td class="numeric">'+esc(amount(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote))+'</td><td class="numeric price-value">'+esc(price(e.price))+'</td><td>Uniswap V3<small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/100)+' б.п.</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):'<tr><td colspan="7" class="empty">Подтверждённых покупок и продаж этого адреса в отслеживаемых пулах не найдено.</td></tr>';
  el("address-page").textContent=data.total?count(data.offset+1)+"–"+count(Math.min(data.total,data.offset+data.limit))+" из "+count(data.total):"0 сделок";
  el("address-data").hidden=false;
 }
 async function refresh(){
  if(!dialog.open)return;
  wallet.abort?.abort();const controller=wallet.abort=new AbortController(),id=++wallet.request;
  const timer=setTimeout(()=>controller.abort(),30000);wallet.loading=true;
  el("address-status").textContent="Загружаем покупки и продажи за всю историю…";
  dialog.setAttribute("aria-busy","true");
  el("address-prev").disabled=true;el("address-next").disabled=true;
  try{
   const response=await fetch("/api/mints/flows?"+new URLSearchParams({q:wallet.address,side:"all",hours:0,limit:25,offset:wallet.offset,sort:wallet.sort}),{signal:controller.signal});
   if(!response.ok)throw new Error("HTTP "+response.status);
   const data=await response.json();if(id!==wallet.request||!dialog.open)return;
   wallet.data=data;render(data);
  }catch(error){if(id===wallet.request&&dialog.open)el("address-status").textContent="Не удалось обновить сделки. "+(wallet.data?"Ниже сохранён предыдущий результат. ":"")+"Нажмите «Обновить сделки». "+(error.name==="AbortError"?"Таймаут.":error.message);}
  finally{
   clearTimeout(timer);
   if(id===wallet.request){wallet.loading=false;dialog.setAttribute("aria-busy","false");
    el("address-prev").disabled=!wallet.data?.ready||wallet.data.offset===0;
    el("address-next").disabled=!wallet.data?.ready||!wallet.data.has_more;
   }
  }
 }
 function open(address,opener){
  if(!/^0x[0-9a-f]{40}$/.test(address))return;
  wallet.abort?.abort();
  Object.assign(wallet,{address,opener,offset:0,sort:"time_desc",data:null});
  el("address-data").hidden=true;el("address-copy").textContent="Копировать";
  el("address-link").textContent=address+" ↗";el("address-link").href="https://etherscan.io/address/"+address;
  if(!dialog.open)dialog.showModal();
  refresh();
 }
 setInterval(()=>{if(dialog.open&&!document.hidden&&!wallet.loading)refresh();},30000);
 return {open};
})();
