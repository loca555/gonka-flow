/* Gross attributed swap volume, not holder balances, profit or beneficial ownership. */
(()=>{
 const ranking={data:null,loading:false,request:0,startDate:'',priceStep:'5',grouped:true,sort:{buy:'volume_desc',sell:'volume_desc'}};
 try{ranking.grouped=localStorage.getItem('gonka-leaders-grouped')!=='0';}catch{}
 const dateInput=el('leaders-start-date'),dateStorageKey='gonka-leaders-start-date';
 try{dateInput.value=localStorage.getItem(dateStorageKey)||'';}catch{}
 ranking.startDate=dateInput.value;
 const periodLabel=value=>value?'С '+value.split('-').reverse().join('.'):'Вся история';
 function showPeriod(){
  el('leaders-period').textContent=periodLabel(ranking.startDate);
  el('leaders-period-reset').disabled=!ranking.startDate&&!dateInput.value;
 }
 function applyPeriod(value){
  dateInput.value=value;
  if(value===ranking.startDate){showPeriod();if(!ranking.data?.ready)refresh();return;}
  ranking.startDate=value;
  try{if(value)localStorage.setItem(dateStorageKey,value);else localStorage.removeItem(dateStorageKey);}catch{}
  showPeriod();refresh(true);
 }
 el('leaders-period-form').addEventListener('submit',event=>{event.preventDefault();if(dateInput.reportValidity())applyPeriod(dateInput.value);});
 el('leaders-period-reset').addEventListener('click',()=>applyPeriod(''));
 dateInput.addEventListener('input',()=>{el('leaders-period-reset').disabled=!ranking.startDate&&!dateInput.value;});
 dateInput.addEventListener('click',()=>{try{dateInput.showPicker?.();}catch{}});
 showPeriod();
 const sides=[{key:'buy',list:'buyers',title:'Крупнейшие покупатели',verb:'Куплено',label:'Покупки'},
              {key:'sell',list:'sellers',title:'Крупнейшие продавцы',verb:'Продано',label:'Продажи'}];
 el('leaders-content').innerHTML='<div class="leader-price-controls"><label class="leader-group-toggle"><input type="checkbox" id="leaders-grouped"'+(ranking.grouped?' checked':'')+'> Объединять вероятные группы адресов</label><p>Объём WGNK по цене исполнения</p><label for="leaders-price-step">Шаг цены <select id="leaders-price-step"><option value="5">0,05 USDT</option><option value="10">0,10 USDT</option></select></label></div><p class="leader-days-note">Один день — один диапазон с наибольшим общим объёмом покупок и продаж. Дни без сделок не учитываются · время Кипра.</p><details id="leaders-bridge-note" class="leader-bridge-note"></details><div class="leaders-grid">'+sides.map(side=>
  '<section class="panel leader-panel '+side.key+'" aria-labelledby="leaders-'+side.key+'-title">'+
  '<div class="leader-heading"><h2 id="leaders-'+side.key+'-title">'+side.title+'</h2><span id="leaders-'+side.key+'-count"></span></div>'+
  '<div class="leader-total"><strong id="leaders-'+side.key+'-total">—</strong><span>WGNK</span><small>Оборот адресов в рейтинге</small></div>'+
  '<section class="leader-chart-panel" aria-labelledby="leaders-'+side.key+'-chart-title">'+
  '<div class="leader-chart-heading"><h3 id="leaders-'+side.key+'-chart-title">'+side.label+' по цене</h3>'+
  '<div class="market-chart-legend"><span class="trade-key '+side.key+'">Объём WGNK</span><span class="count-key">Количество сделок</span></div></div>'+
  '<div id="leaders-'+side.key+'-chart" class="interactive-chart"></div><div id="leaders-'+side.key+'-price-summary" class="leader-price-summary"></div></section>'+
  '<div class="table-container leader-table" tabindex="0" aria-label="'+side.title+', прокручиваемый список">'+
  '<table id="leaders-'+side.key+'-table"><thead><tr><th data-sort="rank" data-default="asc">№</th><th data-sort="address" data-default="asc">Адрес</th>'+
  '<th data-sort="volume" class="numeric">'+side.verb+' · WGNK</th><th data-sort="quote" class="numeric">Сумма · USDT</th>'+
  '<th data-sort="pnl" data-default="desc" class="numeric" title="Реализованный PNL: получено от продаж − уплачено за покупки, USDT. Только для адресов с обеими сторонами; волатильность меньше ±100 USDT скрыта">PNL · USDT</th>'+
  '<th data-sort="price" class="numeric">Цена · USDT</th><th data-sort="swaps" class="numeric">Исполнений</th></tr></thead>'+
  '<tbody id="leaders-'+side.key+'-rows"></tbody></table></div></section>').join('')+'</div>';
 for(const side of sides)configureTableSort('leaders-'+side.key+'-table',ranking.sort[side.key],sort=>{
  ranking.sort[side.key]=sort;if(ranking.data?.ready)renderTable(side,ranking.data,true);
 });
 function renderBridgeNote(data){
  const host=el('leaders-bridge-note'),bridge=data.bridge,mapped=data.price_bridge?.[ranking.priceStep];
  if(!bridge?.ready||!mapped){setLiveHTML(host,'<summary>Объёмы моста пока не подтверждены</summary><p>Для расчёта нужна полная история моста и проверка стартового пополнения пула.</p>');return;}
  const adjustment=bridge.adjustment,excluded=BigInt(adjustment.amount_raw),unassigned=mapped.unassigned;
  const link=(kind,value,label)=>'<a href="https://etherscan.io/'+kind+'/'+esc(value)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>';
  let text='<summary>Мост в подсказках · ввод и вывод за дни уровня'+(excluded?' · исключено '+GonkaChart.exact(excluded,9)+' WGNK стартовой ликвидности':'')+'</summary>'+
   '<p>Объём моста относится ко всему дню и одинаков на обоих графиках. Это не объём покупок или продаж.</p>';
  if(excluded)text+='<p>Из ввода за 09.06.2026 вычтена стартовая ликвидность: '+GonkaChart.exact(excluded,9)+' WGNK. Адрес '+link('address',adjustment.origin,short(adjustment.origin,6))+
   ' передал средства через '+link('address',adjustment.relay,short(adjustment.relay,6))+' в '+link('address',adjustment.pool,'пул')+'. Пополнения: '+
   adjustment.deposits.map((d,i)=>link('tx',d.tx_hash,String(i+1))).join(' · ')+'. Это корректировка показателя; исходные переводы сохранены.</p>';
  if(unassigned.days)text+='<p>Дни без сделок в рейтинге: '+count(unassigned.days)+'. Вне ценовых диапазонов: ввод '+GonkaChart.exact(unassigned.in_raw,9)+' WGNK, вывод '+GonkaChart.exact(unassigned.out_raw,9)+' WGNK.</p>';
  setLiveHTML(host,text);host.dataset.excluded=adjustment.amount_raw;
 }
 function renderCharts(data){
  if(!data?.ready||el('leaders-view').hidden)return;
  const distribution=data.price_distribution[ranking.priceStep],all=[...distribution.buy,...distribution.sell];
  let bounds=[...new Set(all.map(row=>row.from_price_raw))].sort((a,b)=>BigInt(a)<BigInt(b)?-1:BigInt(a)>BigInt(b)?1:0);
  const step=BigInt(ranking.priceStep)*10000000000n;
  let sparse=false;
  if(bounds.length){
   const low=BigInt(bounds[0]),high=BigInt(bounds[bounds.length-1]);
   sparse=(high-low)/step>40n;
   if(!sparse){bounds=[];for(let value=low;value<=high;value+=step)bounds.push(String(value));}
  }
  const maximum=all.reduce((max,row)=>BigInt(row.volume_raw)>max?BigInt(row.volume_raw):max,0n).toString();
  const countMaximum=all.reduce((max,row)=>Math.max(max,row.swaps),0);
  const dayBands=data.price_days?.[ranking.priceStep]?.bands??null;
  const bridgeBands=data.bridge?.ready?data.price_bridge?.[ranking.priceStep]?.bands??null:null;
  renderBridgeNote(data);
  for(const side of sides){
   const points=distribution[side.key],host=el('leaders-'+side.key+'-chart');
   const options={side:side.key,bounds,stepRaw:step.toString(),maximum,countMaximum,dayBands,bridgeBands,sparse};
   renderLiveChart(host,[points,options],()=>GonkaChart.renderPriceBands(host,points,options));
   const summary=el('leaders-'+side.key+'-price-summary'),total=data.summary[side.key];
   const peak=points.reduce((best,row)=>!best||BigInt(row.volume_raw)>BigInt(best.volume_raw)?row:best,null);
   summary.hidden=!peak;
   if(peak){
    const average=BigInt(total.quote_raw)*1000000000000000n/BigInt(total.volume_raw);
    setLiveHTML(summary,'<div><span>Больше всего объёма</span><strong>'+GonkaChart.priceRange(peak.from_price_raw,peak.to_price_raw)+' <small>USDT</small></strong><small>'+GonkaChart.formatShare(peak.volume_raw,total.volume_raw)+' объёма · '+amount(peak.volume)+' WGNK</small></div>'+
     '<div><span>Средневзвешенная цена</span><strong>'+GonkaChart.formatPrice(average)+' <small>USDT</small></strong><small>за 1 WGNK · '+esc(periodLabel(data.start_date).toLowerCase())+'</small></div>');
   }
  }
 }
 el('leaders-price-step').addEventListener('change',event=>{
  ranking.priceStep=event.target.value;renderCharts(ranking.data);
 });
 el('leaders-grouped').addEventListener('change',event=>{
  ranking.grouped=event.target.checked;
  try{localStorage.setItem('gonka-leaders-grouped',ranking.grouped?'1':'0');}catch{}
  refresh(true);
 });
 const chartResize=new ResizeObserver(()=>renderCharts(ranking.data));
 for(const side of sides)chartResize.observe(el('leaders-'+side.key+'-chart'));
 function pnlCell(row){
  if(row.pnl_raw==null)return '<td class="numeric" title="Только покупки или только продажи, либо PNL в пределах ±100 USDT"></td>';
  const neg=row.pnl_raw.startsWith("-");
  return '<td class="numeric" title="Реализованный PNL: продажи − покупки, USDT"><span class="'+(neg?'pnl-neg':'pnl-pos')+'">'+(neg?'−':'+')+' '+esc(amount(row.pnl))+'</span></td>';
 }
 function renderTable(side,data,reset=false){
  const table=el('leaders-'+side.key+'-table'),scroll=table.parentElement,top=reset?0:scroll.scrollTop,left=scroll.scrollLeft;
  const [field,direction]=ranking.sort[side.key].split('_'),sign=direction==='asc'?1:-1;
  const value=row=>({rank:row.rank,address:row.address,volume:BigInt(row.volume_raw),quote:BigInt(row.quote_raw),swaps:row.swaps,pnl:row.pnl_raw==null?-Infinity:BigInt(row.pnl_raw)})[field];
  const rows=[...data[side.list]].sort((a,b)=>{
   const x=field==='price'?BigInt(a.quote_raw)*BigInt(b.volume_raw):value(a);
   const y=field==='price'?BigInt(b.quote_raw)*BigInt(a.volume_raw):value(b);
   return (x<y?-1:x>y?1:0)*sign||a.address.localeCompare(b.address);
  });
  setTableSort(table.id,ranking.sort[side.key]);
  el('leaders-'+side.key+'-count').textContent=count(rows.length)+' адресов';
  el('leaders-'+side.key+'-total').textContent=amount(data.summary[side.key].volume);
  const maximum=BigInt(data[side.list][0]?.volume_raw||'0');
  setLiveHTML(el('leaders-'+side.key+'-rows'),rows.length?rows.map(row=>{
   const width=maximum?Number(BigInt(row.volume_raw)*10000n/maximum)/100:0;
   const links=(row.group?.evidence||[]).map(e=>esc(e.label)).filter((v,i,a)=>a.indexOf(v)===i).join(', ');
   const cell=row.group
    ?'<details class="leader-group"><summary><strong>Группа · '+count(row.group.addresses.length)+' адресов</strong><small> '+(links||'связанные адресы')+'</small></summary>'+
      '<ul>'+row.group.addresses.map(a=>'<li><button class="address-button" data-flow-address="'+esc(a)+'" title="'+esc(a)+'">'+esc(short(a,8))+'</button></li>').join('')+'</ul></details>'
    :'<button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button>';
   return '<tr data-address="'+esc(row.address)+'"><td class="leader-rank">'+count(row.rank)+'</td>'+
    '<td>'+cell+'</td>'+
    '<td class="numeric leader-volume" data-raw="'+esc(row.volume_raw)+'" title="'+esc(row.volume)+' WGNK"><strong>'+esc(amount(row.volume))+'</strong><span class="leader-volume-bar" style="width:'+width+'%" aria-hidden="true"></span></td>'+
    '<td class="numeric" title="'+esc(row.quote)+' USDT">'+esc(amount(row.quote))+'</td>'+
    pnlCell(row)+
    '<td class="numeric price-value" title="Средневзвешенная цена за 1 WGNK">'+esc(price(row.average_price))+'</td>'+
    '<td class="numeric" title="'+count(row.transactions)+' транзакций">'+count(row.swaps)+'</td></tr>';
  }).join(''):'<tr><td colspan="7" class="empty">Пока нет сделок с подтверждённой привязкой к адресу за выбранный период.</td></tr>');
  scroll.scrollTop=top;scroll.scrollLeft=left;
 }
 function render(data){
  if(!data.ready){
   el('leaders-status').textContent='Проверенный снимок торговли пока не готов. Это не означает отсутствие покупок и продаж.';
   el('leaders-content').hidden=true;el('leaders-excluded').hidden=true;return;
  }
  el('leaders-content').hidden=false;
  const snapshot=data.snapshot,stale=data.status.error||data.now-snapshot.checked_at>180;
  el('leaders-status').textContent='Снимок #'+count(snapshot.height)+' · '+date(snapshot.ts)+' '+clock(snapshot.ts)+
   (data.coverage.complete?' · история без пропусков':' · история неполная, догружаем '+count(data.coverage.missing)+' блоков')+
   (stale?' · свежие данные временно недоступны, показан сохранённый снимок':'');
  for(const side of sides)renderTable(side,data);
  renderCharts(data);
  el('leaders-excluded').hidden=BigInt(data.excluded.buy.volume_raw)+BigInt(data.excluded.sell.volume_raw)===0n;
  el('leaders-excluded').textContent='Не вошли в рейтинг: покупки '+amount(data.excluded.buy.volume)+' WGNK ('+count(data.excluded.buy.swaps)+
   ' исполнений) и продажи '+amount(data.excluded.sell.volume)+' WGNK ('+count(data.excluded.sell.swaps)+' исполнений) — адрес участника не установлен. Прочие сделки отнесены участнику по чистому потоку WGNK в транзакции, при отсутствии — инициатору.';
 }
 async function refresh(periodChanged=false){
  if(ranking.loading&&!periodChanged)return;
  ranking.abort?.abort();
  if(periodChanged){
   ranking.data=null;el('leaders-content').hidden=true;el('leaders-excluded').hidden=true;
   for(const side of sides)el('leaders-'+side.key+'-table').parentElement.scrollTop=0;
  }
  const id=++ranking.request,controller=ranking.abort=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  ranking.loading=true;
  if(!ranking.data){el('leaders-view').setAttribute('aria-busy','true');el('leaders-status').textContent='Считаем крупнейших покупателей и продавцов…';}
  try{
   const params={};
   if(ranking.startDate)params.start_date=ranking.startDate;
   if(!ranking.grouped)params.grouped='false';
   const query=Object.keys(params).length?'?'+new URLSearchParams(params):'';
   const response=await fetch('/api/mints/leaders'+query,{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==ranking.request)return;
   if(ranking.data?.ready&&!data.ready){el('leaders-status').textContent='Сервис восстанавливает снимок. Последний проверенный рейтинг сохранён; повторим автоматически.';return;}
   ranking.data=data;render(data);
  }catch(error){if(id===ranking.request)el('leaders-status').textContent='Не удалось обновить рейтинг. '+
   (ranking.data?.ready?'Предыдущие данные сохранены. ':'')+(error.name==='AbortError'?'Сервер не ответил вовремя.':error.message);}
  finally{clearTimeout(timer);if(id===ranking.request){ranking.loading=false;el('leaders-view').setAttribute('aria-busy','false');}}
 }
 window.addEventListener('gonka:view',()=>{if(!el('leaders-view').hidden)refresh();});
 setInterval(()=>{if(!document.hidden&&!el('leaders-view').hidden)refresh();},20000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!el('leaders-view').hidden)refresh();});
 if(!el('leaders-view').hidden)refresh();
})();
