/* Market views are independent of the mint-size filter. Only finalized, verified pool data. */
(()=>{
 const ui=id=>document.getElementById(id);
 const flow={hours:0,q:"",offset:0,side:"all",sort:"time_desc",minterSort:"sold_desc",data:null,loading:false,request:0};
 const fmt=value=>value===null||value===undefined?"—":amount(value,4);
 const addr=value=>'<button class="address-button" data-flow-address="'+esc(value)+'" title="'+esc(value)+'">'+esc(short(value,10))+'</button>';
 const holderKinds={
  investors:{label:"Инвесторы",rule:"Покупки > 90% · продажи < 10%"},
  sellers:{label:"Продавцы",rule:"Продажи > 90% · покупки < 10%"},
  traders:{label:"Трейдеры",rule:"Покупки и продажи: от 10% до 90%"},
  unclassified:{label:"Без торговой истории",rule:"Нет подтверждённых покупок и продаж"}
 };
 const holderView={data:null,group:null,sort:"balance_desc",opener:null};
 const share=(raw,total)=>{
  const n=BigInt(raw),d=BigInt(total);
  return !d?"—":n>0n&&n*100n<d?"< 1%":(n*100n/d).toString()+"%";
 };
 function mountHolders(){
  ui("distribution-legend").insertAdjacentHTML("afterend",'<section id="outside-holders" class="outside-holders" aria-labelledby="outside-holders-title"><div class="holder-heading"><h3 id="outside-holders-title">Вне пулов · распределение баланса</h3><p id="outside-holders-total"></p></div><div id="outside-holders-content" hidden><div id="holder-distribution-bar" class="holder-distribution-bar" role="img"></div><div id="holder-group-cards" class="holder-group-cards"></div><p class="holder-method">Категория по объёму WGNK за всю историю: покупки / (покупки + продажи), аналогично для продаж. Только подтверждённые сделки двух пулов; переводы и мост не являются сделками. Ровно 90/10 — трейдеры. Условные категории адресов, не установленные личности. Доля — от текущего баланса вне пулов, не от торгового оборота.</p></div></section>');
  document.body.insertAdjacentHTML("beforeend",'<dialog id="holder-dialog" aria-labelledby="holder-dialog-title"><div class="dialog-heading"><div><span class="eyebrow">WGNK / ВНЕ ПУЛОВ</span><h2 id="holder-dialog-title"></h2></div><button id="holder-close" aria-label="Закрыть список адресов" autofocus>✕</button></div><p id="holder-dialog-summary" class="holder-dialog-summary"></p><p id="holder-dialog-rule" class="flow-explanation"></p><div class="table-container holder-address-table" tabindex="0" aria-label="Все адреса категории, прокручиваемый список"><table id="holder-table"><thead><tr><th data-sort="address" data-default="asc">Адрес Ethereum</th><th data-sort="balance" class="numeric">Баланс WGNK</th><th data-sort="share" class="numeric">Доля группы</th><th data-sort="bought" class="numeric">Куплено WGNK</th><th data-sort="sold" class="numeric">Продано WGNK</th><th data-sort="buyshare" class="numeric">Доля покупок</th></tr></thead><tbody id="holder-rows"></tbody></table></div><p id="holder-dialog-note" class="flow-explanation"></p></dialog>');
  ui("holder-group-cards").insertAdjacentHTML("afterend",'<article class="holder-history-panel" aria-labelledby="holder-history-title"><div class="holder-history-heading"><div><h3 id="holder-history-title">Баланс категорий во времени</h3><p id="holder-history-period">С момента создания моста · по дням</p></div><div class="holder-history-legend">'+Object.entries(holderKinds).map(([key,value])=>'<span class="holder-'+key+'"><i></i>'+value.label+'</span>').join('')+'</div></div><div id="holder-history-chart" class="interactive-chart"></div><div class="holder-history-footer"><span id="holder-history-snapshot"></span><span>Наведите курсор или коснитесь графика</span></div><p class="holder-history-note">Баланс на конец дня; последний день — на проверенном снимке. Категория адреса определяется по его сделкам к этому дню. При смене категории весь остаток переходит на другую линию — это не обязательно перевод токенов.</p></article>');
  const dialog=ui("holder-dialog");closeOnBackdrop(dialog);
  ui("holder-close").addEventListener("click",()=>dialog.close());
  dialog.addEventListener("close",()=>{
   const opener=holderView.opener?.isConnected?holderView.opener:document.querySelector('[data-holder-group="'+holderView.group?.id+'"]');
   opener?.focus({preventScroll:true});
  });
  configureTableSort("holder-table",holderView.sort,sort=>{holderView.sort=sort;renderHolderRows();});
  ui("holder-group-cards").addEventListener("click",event=>{
   const button=event.target.closest("[data-holder-group]"),data=flow.data?.outside_holders;
   if(!button||!data?.ready)return;
   const group=data.groups.find(g=>g.id===button.dataset.holderGroup);if(!group)return;
   Object.assign(holderView,{data,group,opener:button,sort:"balance_desc"});
   ui("holder-dialog-title").textContent=holderKinds[group.id].label;
   ui("holder-dialog-summary").textContent=fmt(group.balance)+" WGNK · "+share(group.balance_raw,data.total_raw)+" от WGNK вне пулов · адресов: "+count(group.addresses);
   ui("holder-dialog-rule").textContent=holderKinds[group.id].rule+". Объём = куплено + продано WGNK за всю подтверждённую историю двух пулов. Нажмите на адрес, чтобы открыть график и сделки.";
   ui("holder-dialog-note").textContent="Снимок #"+count(data.height)+" · "+date(data.ts)+" "+clock(data.ts)+". Все адреса с положительным балансом одним списком. Доля группы — часть её текущего баланса; доля покупок — часть оборота адреса. Проценты показаны без дробной части, категория рассчитывается по точным значениям.";
   renderHolderRows();dialog.showModal();dialog.scrollTop=0;dialog.querySelector(".holder-address-table").scrollTop=0;
  });
 }
 function renderHolderRows(){
  const group=holderView.group;if(!group)return;
  const [field,direction]=holderView.sort.split("_"),sign=direction==="asc"?1:-1;
  const compare=(a,b)=>a<b?-1:a>b?1:0;
  const rows=[...group.holders].sort((a,b)=>{
   let order;
   if(field==="buyshare"){
    const av=BigInt(a.bought_raw)+BigInt(a.sold_raw),bv=BigInt(b.bought_raw)+BigInt(b.sold_raw);
    if(!av||!bv){if(Boolean(av)!==Boolean(bv))return av?-1:1;order=0;}
    else order=compare(BigInt(a.bought_raw)*bv,BigInt(b.bought_raw)*av);
   }else{
    const key=field==="share"?"balance":field;
    order=key==="address"?compare(a.address,b.address):compare(BigInt(a[key+"_raw"]),BigInt(b[key+"_raw"]));
   }
   return sign*order||compare(a.address,b.address);
  });
  setTableSort("holder-table",holderView.sort);
  ui("holder-rows").innerHTML=rows.length?rows.map(r=>'<tr data-holder-address="'+esc(r.address)+'"><td>'+addr(r.address)+'</td><td class="numeric" data-raw="'+r.balance_raw+'">'+esc(fmt(r.balance))+'</td><td class="numeric">'+esc(share(r.balance_raw,group.balance_raw))+'</td><td class="numeric">'+esc(fmt(r.bought))+'</td><td class="numeric">'+esc(fmt(r.sold))+'</td><td class="numeric">'+esc(share(r.bought_raw,(BigInt(r.bought_raw)+BigInt(r.sold_raw)).toString()))+'</td></tr>').join(""):'<tr><td colspan="6" class="empty">В этой категории пока нет адресов с положительным балансом.</td></tr>';
 }
 function renderHolders(data){
  const info=data.outside_holders;
  ui("outside-holders-content").hidden=!info?.ready;
  if(!info?.ready){ui("outside-holders-total").textContent="Разбивка баланса пока не подтверждена — это не нулевые значения.";return;}
  ui("outside-holders-total").textContent="Доли от "+fmt(info.total)+" WGNK вне двух отслеживаемых пулов · адресов: "+count(info.addresses);
  const total=BigInt(info.total_raw);
  ui("holder-distribution-bar").setAttribute("aria-label",info.groups.map(g=>holderKinds[g.id].label+": "+share(g.balance_raw,info.total_raw)).join(", "));
  ui("holder-distribution-bar").innerHTML=info.groups.map(g=>'<span class="holder-'+g.id+'" style="width:'+(total?Number(BigInt(g.balance_raw)*100000n/total)/1000:0)+'%" title="'+esc(holderKinds[g.id].label+": "+fmt(g.balance)+" WGNK")+'"></span>').join("");
  ui("holder-group-cards").innerHTML=info.groups.map(g=>'<button type="button" class="holder-group holder-'+g.id+'" data-holder-group="'+g.id+'" data-balance-raw="'+g.balance_raw+'" aria-haspopup="dialog" aria-controls="holder-dialog"><span class="holder-group-name"><i></i>'+holderKinds[g.id].label+'<span aria-hidden="true">↗</span></span><strong>'+esc(fmt(g.balance))+' <small>WGNK</small></strong><span class="holder-group-share">'+esc(share(g.balance_raw,info.total_raw))+' <small>от WGNK вне пулов</small></span><small class="holder-group-rule">'+esc(holderKinds[g.id].rule)+'</small><span class="holder-group-count">Адресов: '+count(g.addresses)+' <span>Открыть список →</span></span></button>').join("");
 }
 function mount(){
  ui("flow-content").innerHTML=
  '<article class="panel distribution-panel"><div class="panel-title"><div><h2>Где находятся выпущенные WGNK</h2><p>Вся эмиссия · независимо от фильтров чеканки и сделок</p></div><span class="eyebrow">СНИМОК</span></div><div class="distribution-total"><strong id="distribution-total">—</strong><span>WGNK выпущено с создания моста</span></div><div id="distribution-bar" class="distribution-bar" role="img" aria-label="В пулах, вне отслеживаемых пулов и сожжено"></div><div id="distribution-legend" class="distribution-legend"></div><div id="flow-pools" class="flow-pools"></div><p class="flow-explanation">Баланс пулов включает WGNK от добавления ликвидности, обменов и комиссий. Он не равен сумме депозитов LP. Сожжённые WGNK учтены только на Ethereum; завершение обратного моста здесь не проверяется.</p></article>'+
  '<div class="sales-heading"><div><h2>Торговля</h2><p>Все адреса, не только минтеры · два пула Uniswap V3 · WGNK / USDT</p></div><label>Период сделок <select id="sales-period"><option value="0">Вся история</option><option value="720">30 дней</option><option value="168">7 дней</option><option value="24">24 часа</option></select></label></div>'+
  '<form id="sales-search" class="sales-search"><input id="sales-query" aria-label="Поиск сделок" placeholder="Полный адрес или хеш транзакции — 0x…" maxlength="66" autocomplete="off"><button type="button" data-trade-side="all" aria-pressed="true">Все сделки</button><button type="button" data-trade-side="sell" aria-pressed="false">Найти продажи</button><button type="button" data-trade-side="buy" aria-pressed="false">Найти покупки</button><button type="button" id="sales-reset">Сбросить</button></form>'+
  '<p id="sales-result" class="search-result" role="status" aria-live="polite">Загружаем сделки всех адресов…</p>'+
  '<section class="metrics sales-metrics" aria-label="Итоги сделок"><article><span><span id="flow-volume-label">Оборот торгов</span> <small>WGNK</small></span><strong id="flow-sold">—</strong><dl id="flow-volume-breakdown" class="trade-breakdown" hidden></dl><p id="flow-sales-count"></p></article><article><span><span id="flow-quote-label">Оборот USDT</span> <small>USDT</small></span><strong id="flow-proceeds">—</strong><dl id="flow-quote-breakdown" class="trade-breakdown" hidden></dl><p id="flow-quote-note">Сумма USDT по покупкам и продажам, не прибыль</p></article><article><span><span id="flow-price-label">Средняя цена за 1 WGNK</span> <small>USDT</small></span><strong id="flow-price">—</strong><dl id="flow-price-breakdown" class="trade-breakdown" hidden></dl><p>Взвешено по объёму WGNK</p></article></section>'+
  '<p id="trade-explanation" class="flow-explanation">Это валовой оборот: одни и те же токены могут продаваться повторно. Цена = USDT на выходе пула / WGNK на входе, без газа и возможных комиссий маршрутизатора. Другие DEX, CEX и внебиржевые сделки не учтены. Обычные переводы не обозначаются продажами.</p>'+
  '<article class="panel sales-chart-panel"><div class="panel-title"><div><h2 id="sales-chart-title">Цена и объём торгов</h2><p>По дням · все адреса с учётом фильтров сделок</p></div><div class="market-chart-legend"><span class="price-key">Линия · цена</span><span class="trade-key buy" data-chart-side="buy">Покупки</span><span class="trade-key sell" data-chart-side="sell">Продажи</span></div></div><div id="sales-chart" class="interactive-chart"></div><div class="chart-footer"><span id="sales-chart-note">Цена: USDT за 1 WGNK · столбцы: покупки + продажи WGNK</span><span>Наведите курсор или коснитесь графика</span></div></article>'+
  '<div class="section-heading sales-table-heading"><div><h2><span id="trades-table-title">Торговля</span> <span id="sales-total"></span></h2><p>Одна строка — одно исполнение Swap в пуле. Покупки и продажи показаны вместе по умолчанию.</p></div></div>'+
  '<div class="table-container"><table id="trades-table"><thead><tr><th data-sort="time">Дата и время</th><th data-sort="kind" data-default="asc">Сделка</th><th data-sort="actor" data-default="asc">Адрес / инициатор</th><th data-sort="amount" class="numeric">Объём WGNK</th><th data-sort="quote" class="numeric">Сумма USDT</th><th data-sort="price" class="numeric">Цена за 1 WGNK, USDT</th><th data-sort="pool" data-default="asc">Пул</th><th data-sort="tx" data-default="asc">Транзакция</th></tr></thead><tbody id="sales-rows"></tbody></table></div>'+
  '<div class="pagination"><button id="sales-prev">← Назад</button><span id="sales-page"></span><button id="sales-next">Далее →</button></div>';
  ui("minter-report-host").innerHTML='<section class="minter-report"><h2>Минтеры и их продажи <span id="minter-count"></span></h2><p class="flow-explanation">Вся история. Даты — первая и последняя чеканка адреса; сортировка дат — по последней. Продажи могут включать купленные или полученные переводом WGNK: конкретные партии токенов не отслеживаются. Адрес Gonka — проверенный отправитель в мост, не установленный владелец или майнер.</p><p id="minter-status" class="flow-explanation"></p><div class="table-container minter-table"><table id="minter-table"><thead><tr><th data-sort="address" data-default="asc">Минтер · Ethereum</th><th data-sort="native" data-default="asc">Отправитель · Gonka</th><th data-sort="dates">Даты чеканки</th><th data-sort="minted" class="numeric">Получено при чеканке</th><th data-sort="balance" class="numeric">Баланс сейчас</th><th data-sort="sold" class="numeric">Продано адресом</th><th data-sort="price" class="numeric">Средняя цена за 1 WGNK, USDT</th><th data-sort="count">Исполнений продаж</th></tr></thead><tbody id="minter-rows"></tbody></table></div></section>';
  mountHolders();
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
  ui("sales-reset").addEventListener("click",()=>{flow.q="";flow.hours=0;flow.side="all";ui("sales-query").value="";ui("sales-period").value="0";refresh(true);});
  ui("sales-prev").addEventListener("click",()=>{flow.offset=Math.max(0,flow.offset-25);refresh();});
  ui("sales-next").addEventListener("click",()=>{flow.offset+=25;refresh();});
  document.addEventListener("click",event=>{
   const address=event.target.closest("[data-flow-address]");
   if(address){
    el("mint-dialog").close();
    window.WgnkAddress.open(address.dataset.flowAddress,address,address.dataset.gnkAddress||"");
   }
  });
 }
 function renderChart(data){
  if(ui("trading-view").hidden)return;
  const history=data.holder_history;
  GonkaChart.renderGroups(ui("holder-history-chart"),history,holderKinds);
  if(history?.ready){
   ui("holder-history-period").textContent="С создания моста · "+date(history.start_ts)+" — "+date(history.ts)+" · по дням";
   ui("holder-history-snapshot").textContent="Последняя точка · #"+count(history.height)+" · "+date(history.ts)+" "+clock(history.ts);
  }else{
   ui("holder-history-period").textContent="История категорий ещё не подтверждена";
   ui("holder-history-snapshot").textContent="Недоступные данные не заменяются нулями";
  }
  ui("sales-chart-title").textContent="Цена и объём "+(data.side==="all"?"торгов":data.side==="buy"?"покупок":"продаж");
  document.querySelectorAll("[data-chart-side]").forEach(key=>{key.hidden=data.side!=="all"&&key.dataset.chartSide!==data.side;});
  GonkaChart.renderMarket(ui("sales-chart"),data.daily,{side:data.side});
  ui("sales-chart-note").textContent="Средневзвешенная цена: USDT за 1 WGNK · "+(data.side==="all"?"столбцы: покупки + продажи WGNK":"объём: WGNK");
 }
 function renderBreakdown(id,visible,rows){
  const host=ui(id);host.hidden=!visible;
  host.innerHTML=visible?rows.map(r=>'<div data-trade-kind="'+r.side+'"><dt><span class="trade-key '+r.side+'">'+(r.side==="buy"?"Покупки":"Продажи")+'</span>'+(r.note?'<small>'+esc(r.note)+'</small>':'')+'</dt><dd><b>'+esc(r.value)+'</b> <small>'+esc(r.unit)+'</small></dd></div>').join(""):"";
 }
 function renderMinters(data){
  const [field,direction]=flow.minterSort.split("_"),sign=direction==="asc"?1:-1;
  const raw=value=>BigInt(String(value).split(".")[0]+(String(value).split(".")[1]||"").padEnd(9,"0"));
  const key=r=>({address:r.address,native:(r.gnk_addresses||[]).join(','),dates:r.last_mint_ts,minted:BigInt(r.minted_raw),balance:raw(r.balance),sold:BigInt(r.sales_raw),count:BigInt(r.sales_count)})[field];
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
  const verified=rows.reduce((n,r)=>n+(r.gnk_verified_mints||0),0),totalMints=rows.reduce((n,r)=>n+r.mint_count,0);
  ui("minter-status").textContent="Ethereum: снимок #"+count(data.snapshot.height)+" · "+date(data.snapshot.ts)+" "+clock(data.snapshot.ts)+(data.coverage.complete?" · история без пропусков":" · есть незагруженные блоки")+". Связи Gonka: "+count(verified)+" / "+count(totalMints)+" выпусков подтверждено.";
  ui("minter-rows").innerHTML=rows.map(r=>'<tr><td>'+addr(r.address)+'</td><td class="minter-native">'+((r.gnk_addresses||[]).length?r.gnk_addresses.map(a=>'<button class="address-button" data-flow-address="'+esc(r.address)+'" data-gnk-address="'+esc(a)+'" title="'+esc(a)+'">'+esc(short(a,12))+'</button>').join(''):'<span class="pending-badge">Проверяется</span>')+'<small>'+count(r.gnk_verified_mints||0)+' / '+count(r.mint_count)+' выпусков</small></td><td class="mint-dates" data-last-mint="'+r.last_mint_ts+'"><span title="'+clock(r.first_mint_ts)+'">Первая · '+date(r.first_mint_ts)+'</span><small title="'+clock(r.last_mint_ts)+'">Последняя · '+date(r.last_mint_ts)+'</small></td><td class="numeric" data-raw="'+r.minted_raw+'">'+esc(fmt(r.minted))+'</td><td class="numeric" data-balance="'+esc(r.balance)+'">'+esc(fmt(r.balance))+'</td><td class="numeric" data-raw="'+r.sales_raw+'">'+esc(fmt(r.sold))+'</td><td class="numeric price-value">'+esc(price(r.average_price))+'</td><td>'+count(r.sales_count)+'</td></tr>').join("");
 }
 function render(data){
  const summary=data.summary,buy=data.side==="buy",all=data.side==="all";
  if(!data.ready)ui("sales-result").textContent="Сверенный снимок ещё не готов. Сделки пока не показаны.";
  const warning=!data.ready?(data.status.error?"Повторяем проверку торговых данных: "+data.status.error:"Загружаем торговые данные…"):
   data.status.error?"Свежие торговые данные временно недоступны. Показаны последние проверенные значения.":
   data.now-data.snapshot.checked_at>180?"Торговые данные обновляются. Показаны последние проверенные значения.":"";
  ui("trade-status").textContent=warning;ui("trade-status").hidden=!warning;
  ui("flow-content").hidden=!data.ready;
  if(!data.ready)return;
  const noun=all?"Торговля":buy?"Покупки":"Продажи";
  ui("sales-result").textContent=noun+" · "+(data.q?data.q:"все адреса")+" · найдено "+count(data.total)+" исполнений"+
   (data.hours?" · за "+count(data.hours/24)+" дней":" · вся история");
  document.querySelectorAll("[data-trade-side]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.tradeSide===data.side)));
  ui("flow-volume-label").textContent=all?"Оборот торгов":buy?"Куплено из пулов":"Продано в пулах";
  ui("flow-quote-label").textContent=all?"Оборот USDT":buy?"Уплачено в пулы":"Выдано пулами";
  ui("flow-quote-note").textContent=all?"Сумма USDT по покупкам и продажам, не прибыль":buy?"Вход USDT по этим покупкам":"Выход USDT по этим продажам";
  ui("flow-price-label").textContent="Средняя цена за 1 WGNK";
  ui("flow-price-label").nextElementSibling.textContent="USDT";
  ui("trades-table-title").textContent="Торговля";
  ui("trade-explanation").textContent="Все адреса в двух отслеживаемых пулах, не только минтеры. Валовой оборот включает повторные сделки. Цены показаны в USDT за 1 WGNK, без газа и возможных комиссий маршрутизатора. Переводы и ликвидность не являются сделками. Другие DEX, CEX и OTC не учтены.";
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
  renderHolders(data);
  ui("flow-pools").innerHTML=data.pools.map(p=>'<a class="pool-state" href="https://etherscan.io/address/'+p.address+'" target="_blank" rel="noopener noreferrer"><span>Uniswap V3 · '+count(p.fee/100)+' б.п. <small>'+esc(short(p.address))+' ↗</small></span><strong>'+esc(fmt(p.balance))+' <small>WGNK</small></strong></a>').join("");
  ui("flow-sold").textContent=compact(summary.volume);ui("flow-sold").title=amount(summary.volume,9)+" WGNK";
  ui("flow-sales-count").textContent=count(summary.swaps)+" исполнений · "+count(summary.transactions)+" транзакций";
  ui("flow-proceeds").textContent=compact(summary.quote);ui("flow-proceeds").title=amount(summary.quote,6)+" USDT";
  ui("flow-price").textContent=price(summary.average_price);ui("flow-price").title="USDT за 1 WGNK";
  renderBreakdown("flow-volume-breakdown",all,[
   {side:"buy",value:fmt(summary.bought),unit:"WGNK",note:count(summary.buys_count)+" исполнений"},
   {side:"sell",value:fmt(summary.sold),unit:"WGNK",note:count(summary.sales_count)+" исполнений"}]);
  renderBreakdown("flow-quote-breakdown",all,[
   {side:"buy",value:fmt(summary.buy_quote),unit:"USDT",note:"Уплачено в пулы"},
   {side:"sell",value:fmt(summary.sale_quote),unit:"USDT",note:"Получено из пулов"}]);
  renderBreakdown("flow-price-breakdown",all,[
   {side:"buy",value:price(summary.buy_average_price),unit:"USDT"},
   {side:"sell",value:price(summary.sale_average_price),unit:"USDT"}]);
  ui("sales-total").textContent=count(data.total);
  ui("sales-rows").innerHTML=data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+e.kind+'">'+(e.kind==="buy"?"Покупка":"Продажа")+'</span></td><td>'+
   (e.attribution==="initiator_net"?addr(e.actor):'<span class="mono" title="'+esc(e.actor)+'">'+esc(e.actor?short(e.actor,10):"Не установлен")+'</span>')+
   '<small>'+(e.attribution==="initiator_net"?(e.kind==="buy"?"Приток WGNK подтверждён":"Отток WGNK подтверждён"):"Участник не установлен")+'</small></td><td class="numeric">'+esc(fmt(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote))+'</td><td class="numeric price-value">'+esc(price(e.price))+'</td><td><small>Uniswap V3</small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/100)+' б.п.</td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):
   '<tr><td colspan="8" class="empty">Сделок по этим фильтрам в отслеживаемых пулах не найдено.</td></tr>';
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
  ui("sales-result").textContent="Ищем "+(flow.side==="all"?"сделки":flow.side==="buy"?"покупки":"продажи")+"…";
  try{
   const response=await fetch("/api/mints/flows?"+new URLSearchParams({hours:flow.hours,q:flow.q,offset:flow.offset,limit:25,side:flow.side,sort:flow.sort}),{signal:controller.signal});
   if(!response.ok)throw new Error("HTTP "+response.status);
   const data=await response.json();if(id!==flow.request)return;
   flow.data=data;render(data);
  }catch(error){if(id===flow.request){
   ui("trade-status").textContent="Нет свежего ответа по пулам и сделкам. Последние данные сохранены. "+(error.name==="AbortError"?"Таймаут.":error.message);
   ui("trade-status").hidden=false;
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
