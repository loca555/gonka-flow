/* Market views are independent of the mint-size filter. Only finalized, verified pool data. */
(()=>{
 const ui=id=>document.getElementById(id);
 const flow={hours:0,q:"",offset:0,side:"sell",sort:"time_desc",minterSort:"sold_desc",chart:"volume",data:null,loading:false,request:0};
 const fmt=value=>value===null||value===undefined?"—":amount(value,4);
 const addr=value=>'<button class="address-button" data-flow-address="'+esc(value)+'" title="'+esc(value)+'">'+esc(short(value,10))+'</button>';
 function mount(){
  ui("flow-content").innerHTML=
  '<article class="panel distribution-panel"><div class="panel-title"><div><h2>Где находятся выпущенные WGNK</h2><p>Вся эмиссия · независимо от фильтров чеканки и сделок</p></div><span class="eyebrow">СНИМОК</span></div><div class="distribution-total"><strong id="distribution-total">—</strong><span>WGNK выпущено с создания моста</span></div><div id="distribution-bar" class="distribution-bar" role="img" aria-label="В пулах, вне отслеживаемых пулов и сожжено"></div><div id="distribution-legend" class="distribution-legend"></div><div id="flow-pools" class="flow-pools"></div><p class="flow-explanation">Баланс пулов включает WGNK от добавления ликвидности, обменов и комиссий. Он не равен сумме депозитов LP. Сожжённые WGNK учтены только на Ethereum; завершение обратного моста здесь не проверяется.</p></article>'+
  '<div class="sales-heading"><div><h2>Покупки, продажи и цены исполнения</h2><p>Все адреса, не только получатели чеканки · два пула Uniswap V3 · WGNK / USDT</p></div><label>Период сделок <select id="sales-period"><option value="0">Вся история</option><option value="720">30 дней</option><option value="168">7 дней</option><option value="24">24 часа</option></select></label></div>'+
  '<form id="sales-search" class="sales-search"><input id="sales-query" aria-label="Поиск сделок" placeholder="Полный адрес или хеш транзакции — 0x…" maxlength="66" autocomplete="off"><button type="button" data-trade-side="sell" aria-pressed="true">Найти продажи</button><button type="button" data-trade-side="buy" aria-pressed="false">Найти покупки</button><button type="button" id="sales-reset">Сбросить</button></form>'+
  '<p id="sales-result" class="search-result" role="status" aria-live="polite">Загружаем сделки всех адресов…</p>'+
  '<section class="metrics sales-metrics" aria-label="Итоги сделок"><article><span><span id="flow-volume-label">Продано в пулах</span> <small>WGNK</small></span><strong id="flow-sold">—</strong><p id="flow-sales-count"></p></article><article><span><span id="flow-quote-label">Выдано пулами</span> <small>USDT</small></span><strong id="flow-proceeds">—</strong><p id="flow-quote-note">Выход USDT по этим продажам</p></article><article><span><span id="flow-price-label">Средняя цена продажи</span> <small>USDT / WGNK</small></span><strong id="flow-price">—</strong><p>Взвешено по объёму WGNK</p></article></section>'+
  '<p id="trade-explanation" class="flow-explanation">Это валовой оборот: одни и те же токены могут продаваться повторно. Цена = USDT на выходе пула / WGNK на входе, без газа и возможных комиссий маршрутизатора. Другие DEX, CEX и внебиржевые сделки не учтены. Обычные переводы не обозначаются продажами.</p>'+
  '<article class="panel sales-chart-panel"><div class="panel-title"><div><h2 id="sales-chart-title">Объём продаж</h2><p>По дням · все адреса с учётом фильтров сделок</p></div><div class="chart-switch" role="group" aria-label="Показатель графика сделок"><button data-sales-chart="volume" aria-pressed="true">Объём</button><button data-sales-chart="price" aria-pressed="false">Цена</button></div></div><div id="sales-chart" class="interactive-chart"></div><div class="chart-footer"><span id="sales-chart-note">Только финальные обмены</span><span>Наведите курсор или коснитесь графика</span></div></article>'+
  '<div class="section-heading sales-table-heading"><div><h2><span id="trades-table-title">Лента продаж</span> <span id="sales-total"></span></h2><p>Одна строка — одно исполнение Swap в пуле, не весь маршрут агрегатора.</p></div></div>'+
  '<div class="table-container"><table id="trades-table"><thead><tr><th data-sort="time">Дата и время</th><th data-sort="actor" data-default="asc">Адрес / инициатор</th><th data-sort="amount" class="numeric">Объём WGNK</th><th data-sort="quote" class="numeric">Сумма USDT</th><th data-sort="price" class="numeric">Цена USDT/WGNK</th><th data-sort="pool" data-default="asc">Пул</th><th data-sort="tx" data-default="asc">Транзакция</th></tr></thead><tbody id="sales-rows"></tbody></table></div>'+
  '<div class="pagination"><button id="sales-prev">← Назад</button><span id="sales-page"></span><button id="sales-next">Далее →</button></div>';
  ui("minter-report-host").innerHTML='<section class="minter-report"><h2>Получатели чеканки и их продажи <span id="minter-count"></span></h2><p class="flow-explanation">Вся история. Здесь сопоставлены адреса, а не конкретные партии токенов: продажи могут включать купленные или полученные переводом WGNK. Учитываются только инициаторы с подтверждённым оттоком WGNK в receipt. Балансы сверены по полной истории на блок снимка.</p><div class="table-container minter-table"><table id="minter-table"><thead><tr><th data-sort="address" data-default="asc">Получатель чеканки</th><th data-sort="minted" class="numeric">Получено при чеканке</th><th data-sort="balance" class="numeric">Баланс сейчас</th><th data-sort="sold" class="numeric">Продано адресом</th><th data-sort="price" class="numeric">Средняя цена USDT</th><th data-sort="count">Исполнений</th></tr></thead><tbody id="minter-rows"></tbody></table></div></section>';
  function search(side=flow.side){
   const q=ui("sales-query").value.trim().toLowerCase();
   if(q&&!/^0x[0-9a-f]{1,64}$/.test(q)){
    ui("sales-result").textContent="Введите адрес или хеш в формате 0x…";return;
   }
   flow.q=q;flow.side=side;refresh(true);
  }
  ui("sales-search").addEventListener("submit",event=>{event.preventDefault();search();});
  document.querySelectorAll("[data-trade-side]").forEach(button=>button.addEventListener("click",()=>search(button.dataset.tradeSide)));
  configureTableSort("trades-table",flow.sort,sort=>{flow.sort=sort;refresh(true);});
  configureTableSort("minter-table",flow.minterSort,sort=>{flow.minterSort=sort;if(flow.data?.ready)renderMinters(flow.data);});
  ui("sales-period").addEventListener("change",()=>{flow.hours=Number(ui("sales-period").value);refresh(true);});
  ui("sales-reset").addEventListener("click",()=>{flow.q="";flow.hours=0;ui("sales-query").value="";ui("sales-period").value="0";refresh(true);});
  ui("sales-prev").addEventListener("click",()=>{flow.offset=Math.max(0,flow.offset-25);refresh();});
  ui("sales-next").addEventListener("click",()=>{flow.offset+=25;refresh();});
  document.addEventListener("click",event=>{
   const address=event.target.closest("[data-flow-address]");
   if(address){
    flow.q=address.dataset.flowAddress;ui("sales-query").value=flow.q;
    if(address.closest("#minters-view"))flow.side="sell";
    location.hash="mints";showView();refresh(true);
    ui("sales-search").scrollIntoView({behavior:"smooth",block:"center"});
   }
   const chart=event.target.closest("[data-sales-chart]");
   if(chart){
    flow.chart=chart.dataset.salesChart;
    document.querySelectorAll("[data-sales-chart]").forEach(b=>b.setAttribute("aria-pressed",String(b===chart)));
    if(flow.data)renderChart(flow.data);
   }
  });
 }
 function renderChart(data){
  const price=flow.chart==="price",noun=data.side==="buy"?"покупок":"продаж";
  ui("sales-chart-title").textContent=(price?"Средняя цена ":"Объём ")+noun+" · все адреса";
  GonkaChart.render(ui("sales-chart"),{points:data.daily.map(d=>({date:d.date,raw:price?d.price_raw:d.raw,events:d.events})),
   decimals:price?12:9,unit:price?"USDT / WGNK":"WGNK",complete:!price,
   title:(price?"Средняя цена ":"Объём ")+noun+" WGNK по дням",countLabel:"Исполнений"});
  ui("sales-chart-note").textContent=price?"Взвешенная цена дня · дни без сделок — разрыв":"Дневной объём · повторные сделки учитываются";
 }
 function renderMinters(data){
  const [field,direction]=flow.minterSort.split("_"),sign=direction==="asc"?1:-1;
  const raw=value=>BigInt(String(value).split(".")[0]+(String(value).split(".")[1]||"").padEnd(9,"0"));
  const key=r=>({address:r.address,minted:BigInt(r.minted_raw),balance:raw(r.balance),sold:BigInt(r.sales_raw),count:BigInt(r.sales_count)})[field];
  const compare=(a,b)=>a<b?-1:a>b?1:0;
  const rows=[...data.minters].sort((a,b)=>{
   if(field==="price"){
    if(!BigInt(a.sales_raw)||!BigInt(b.sales_raw)){
     if(Boolean(BigInt(a.sales_raw))!==Boolean(BigInt(b.sales_raw)))return BigInt(a.sales_raw)?-1:1;
     return compare(a.address,b.address);
    }
    return sign*compare(BigInt(a.quote_raw)*BigInt(b.sales_raw),BigInt(b.quote_raw)*BigInt(a.sales_raw))||compare(a.address,b.address);
   }
   return sign*compare(key(a),key(b))||compare(a.address,b.address);
  });
  setTableSort("minter-table",flow.minterSort);
  ui("minter-count").textContent=count(rows.length)+" адресов";
  ui("minter-rows").innerHTML=rows.map(r=>'<tr><td>'+addr(r.address)+'</td><td class="numeric" title="'+esc(r.minted)+'">'+esc(fmt(r.minted))+'</td><td class="numeric" title="'+esc(r.balance)+'">'+esc(fmt(r.balance))+'</td><td class="numeric" title="'+esc(r.sold)+'">'+esc(fmt(r.sold))+'</td><td class="numeric">'+(r.average_price===null?"—":esc(amount(r.average_price,6)))+'</td><td>'+count(r.sales_count)+'</td></tr>').join("");
 }
 function render(data){
  const snapshot=data.snapshot,summary=data.summary,buy=data.side==="buy";
  if(!data.ready)ui("sales-result").textContent="Сверенный снимок ещё не готов. Сделки пока не показаны.";
  ui("flow-loading").textContent=!data.ready?(data.status.error?"Повторяем проверку: "+data.status.error:"Проверяем пулы и сверяем историю переводов с totalSupply…"):
   "Снимок #"+count(snapshot.height)+" · "+date(snapshot.ts)+" "+clock(snapshot.ts)+" · "+(data.coverage.complete?"история без пропусков":"догоняем "+count(data.coverage.missing)+" блоков")+
   (data.status.error?" · RPC: "+data.status.error:"");
  ui("flow-content").hidden=!data.ready;
  if(!data.ready)return;
  const noun=buy?"Покупки":"Продажи";
  ui("sales-result").textContent=noun+" · "+(data.q?data.q:"все адреса")+" · найдено "+count(data.total)+" исполнений"+
   (data.hours?" · за "+count(data.hours/24)+" дней":" · вся история");
  document.querySelectorAll("[data-trade-side]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.tradeSide===data.side)));
  ui("flow-volume-label").textContent=buy?"Куплено из пулов":"Продано в пулах";
  ui("flow-quote-label").textContent=buy?"Уплачено в пулы":"Выдано пулами";
  ui("flow-quote-note").textContent=buy?"Вход USDT по этим покупкам":"Выход USDT по этим продажам";
  ui("flow-price-label").textContent=buy?"Средняя цена покупки":"Средняя цена продажи";
  ui("trades-table-title").textContent=buy?"Лента покупок":"Лента продаж";
  ui("trade-explanation").textContent="Все адреса в двух отслеживаемых пулах, не только получатели чеканки. Это валовой оборот: токены могут участвовать в сделках повторно. Цена = "+
   (buy?"USDT на входе пула / WGNK на выходе":"USDT на выходе пула / WGNK на входе")+
   ", без газа и возможных комиссий маршрутизатора. Другие DEX, CEX и внебиржевые сделки не учтены. Обычный перевод не считается сделкой.";
  setTableSort("trades-table",data.sort);
  ui("distribution-total").textContent=fmt(summary.minted);
  const minted=BigInt(summary.minted_raw);
  const segments=[{label:"В отслеживаемых пулах",raw:summary.pooled_raw,value:summary.pooled,color:"var(--accent)"},
   {label:"Вне этих пулов",raw:summary.outside_raw,value:summary.outside_pools,color:"var(--flow-outside)"},
   {label:"Сожжено на Ethereum",raw:summary.burned_raw,value:summary.burned,color:"var(--flow-burned)"}];
  ui("distribution-bar").innerHTML=segments.map(s=>{
   const percent=minted?Number(BigInt(s.raw)*100000n/minted)/1000:0;
   return '<span style="width:'+percent+'%;background:'+s.color+'" title="'+esc(s.label+": "+amount(s.value,9)+" WGNK")+'"></span>';
  }).join("");
  ui("distribution-legend").innerHTML=segments.map(s=>'<div><span><i style="background:'+s.color+'"></i>'+s.label+'</span><strong title="'+esc(amount(s.value,9))+' WGNK">'+esc(fmt(s.value))+' <small>WGNK</small></strong><small>'+(minted?count(Number(BigInt(s.raw)*10000n/minted)/100):"0")+'% от всех выпусков</small></div>').join("");
  ui("flow-pools").innerHTML=data.pools.map(p=>'<a class="pool-state" href="https://etherscan.io/address/'+p.address+'" target="_blank" rel="noopener noreferrer"><span>Uniswap V3 · '+count(p.fee/10000)+'% <small>'+esc(short(p.address))+' ↗</small></span><strong title="'+esc(amount(p.balance,9))+'">'+esc(fmt(p.balance))+' <small>WGNK</small></strong></a>').join("");
  ui("flow-sold").textContent=compact(summary.volume);ui("flow-sold").title=amount(summary.volume,9)+" WGNK";
  ui("flow-sales-count").textContent=count(summary.swaps)+" исполнений · "+count(summary.transactions)+" транзакций";
  ui("flow-proceeds").textContent=compact(summary.quote);ui("flow-proceeds").title=amount(summary.quote,6)+" USDT";
  ui("flow-price").textContent=summary.average_price===null?"—":amount(summary.average_price,6);ui("flow-price").title=summary.average_price||"Сделок не найдено";
  ui("sales-total").textContent=count(data.total);
  ui("sales-rows").innerHTML=data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td>'+
   (e.attribution==="initiator_net"?addr(e.actor):'<span class="mono" title="'+esc(e.actor)+'">'+esc(e.actor?short(e.actor,10):"Не установлен")+'</span>')+
   '<small>'+(e.attribution==="initiator_net"?(buy?"Приток WGNK подтверждён":"Отток WGNK подтверждён"):(buy?"Инициатор, покупатель не установлен":"Инициатор, продавец не установлен"))+'</small></td><td class="numeric" title="'+esc(amount(e.amount,9))+'">'+esc(fmt(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote,6))+'</td><td class="numeric price-value" title="'+esc(e.price)+'">'+esc(amount(e.price,6))+'</td><td><small>Uniswap V3</small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/10000)+'%</td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):
   '<tr><td colspan="7" class="empty">'+(buy?"Покупок":"Продаж")+' по этим фильтрам в отслеживаемых пулах не найдено.</td></tr>';
  ui("sales-prev").disabled=data.offset===0;ui("sales-next").disabled=!data.has_more;
  ui("sales-page").textContent=data.total?count(data.offset+1)+"–"+count(Math.min(data.total,data.offset+data.limit))+" из "+count(data.total):"0 сделок";
  renderMinters(data);
  renderChart(data);
 }
 async function refresh(reset=false){
  if(reset)flow.offset=0;
  flow.abort?.abort();const controller=flow.abort=new AbortController(),id=++flow.request;
  const timer=setTimeout(()=>controller.abort(),30000);flow.loading=true;
  ui("sales-search").setAttribute("aria-busy","true");
  ui("sales-result").textContent="Ищем "+(flow.side==="buy"?"покупки":"продажи")+"…";
  try{
   const response=await fetch("/api/mints/flows?"+new URLSearchParams({hours:flow.hours,q:flow.q,offset:flow.offset,limit:25,side:flow.side,sort:flow.sort}),{signal:controller.signal});
   if(!response.ok)throw new Error("HTTP "+response.status);
   const data=await response.json();if(id!==flow.request)return;
   flow.data=data;render(data);
  }catch(error){if(id===flow.request){
   ui("flow-loading").textContent="Нет свежего ответа по пулам и сделкам. Последние данные сохранены. "+(error.name==="AbortError"?"Таймаут.":error.message);
   ui("sales-result").textContent="Поиск не выполнен. Ниже сохранён предыдущий результат. Повторите запрос.";
  }}
  finally{clearTimeout(timer);if(id===flow.request){flow.loading=false;ui("sales-search").setAttribute("aria-busy","false");}}
 }
 mount();refresh();
 ui("refresh").addEventListener("click",()=>refresh());
 window.addEventListener("resize",()=>{if(flow.data?.ready)renderChart(flow.data);});
 window.addEventListener("gonka:view",()=>{if(flow.data?.ready)renderChart(flow.data);});
 setInterval(()=>{if(!document.hidden&&!flow.loading)refresh();},20000);
})();
