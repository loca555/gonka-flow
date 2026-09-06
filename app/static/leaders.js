/* Gross attributed swap volume, not holder balances, profit or beneficial ownership. */
(()=>{
 const ranking={data:null,loading:false,request:0,sort:{buy:'volume_desc',sell:'volume_desc'}};
 const sides=[{key:'buy',list:'buyers',title:'Крупнейшие покупатели',verb:'Куплено'},
              {key:'sell',list:'sellers',title:'Крупнейшие продавцы',verb:'Продано'}];
 el('leaders-content').innerHTML='<div class="leaders-grid">'+sides.map(side=>
  '<section class="panel leader-panel '+side.key+'" aria-labelledby="leaders-'+side.key+'-title">'+
  '<div class="leader-heading"><h2 id="leaders-'+side.key+'-title">'+side.title+'</h2><span id="leaders-'+side.key+'-count"></span></div>'+
  '<div class="leader-total"><strong id="leaders-'+side.key+'-total">—</strong><span>WGNK</span><small>Оборот адресов в рейтинге</small></div>'+
  '<div class="table-container leader-table" tabindex="0" aria-label="'+side.title+', прокручиваемый список">'+
  '<table id="leaders-'+side.key+'-table"><thead><tr><th data-sort="rank" data-default="asc">№</th><th data-sort="address" data-default="asc">Адрес</th>'+
  '<th data-sort="volume" class="numeric">'+side.verb+' · WGNK</th><th data-sort="quote" class="numeric">Сумма · USDT</th>'+
  '<th data-sort="price" class="numeric">Цена · USDT</th><th data-sort="swaps" class="numeric">Исполнений</th></tr></thead>'+
  '<tbody id="leaders-'+side.key+'-rows"></tbody></table></div></section>').join('')+'</div>';
 for(const side of sides)configureTableSort('leaders-'+side.key+'-table',ranking.sort[side.key],sort=>{
  ranking.sort[side.key]=sort;if(ranking.data?.ready)renderTable(side,ranking.data,true);
 });
 function renderTable(side,data,reset=false){
  const table=el('leaders-'+side.key+'-table'),scroll=table.parentElement,top=reset?0:scroll.scrollTop,left=scroll.scrollLeft;
  const [field,direction]=ranking.sort[side.key].split('_'),sign=direction==='asc'?1:-1;
  const value=row=>({rank:row.rank,address:row.address,volume:BigInt(row.volume_raw),quote:BigInt(row.quote_raw),swaps:row.swaps})[field];
  const rows=[...data[side.list]].sort((a,b)=>{
   const x=field==='price'?BigInt(a.quote_raw)*BigInt(b.volume_raw):value(a);
   const y=field==='price'?BigInt(b.quote_raw)*BigInt(a.volume_raw):value(b);
   return (x<y?-1:x>y?1:0)*sign||a.address.localeCompare(b.address);
  });
  setTableSort(table.id,ranking.sort[side.key]);
  el('leaders-'+side.key+'-count').textContent=count(rows.length)+' адресов';
  el('leaders-'+side.key+'-total').textContent=amount(data.summary[side.key].volume);
  const maximum=BigInt(data[side.list][0]?.volume_raw||'0');
  el('leaders-'+side.key+'-rows').innerHTML=rows.length?rows.map(row=>{
   const width=maximum?Number(BigInt(row.volume_raw)*10000n/maximum)/100:0;
   return '<tr data-address="'+esc(row.address)+'"><td class="leader-rank">'+count(row.rank)+'</td>'+
    '<td><button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button></td>'+
    '<td class="numeric leader-volume" data-raw="'+esc(row.volume_raw)+'" title="'+esc(row.volume)+' WGNK"><strong>'+esc(amount(row.volume))+'</strong><span class="leader-volume-bar" style="width:'+width+'%" aria-hidden="true"></span></td>'+
    '<td class="numeric" title="'+esc(row.quote)+' USDT">'+esc(amount(row.quote))+'</td>'+
    '<td class="numeric price-value" title="Средневзвешенная цена за 1 WGNK">'+esc(price(row.average_price))+'</td>'+
    '<td class="numeric" title="'+count(row.transactions)+' транзакций">'+count(row.swaps)+'</td></tr>';
  }).join(''):'<tr><td colspan="6" class="empty">Пока нет сделок с подтверждённой привязкой к адресу.</td></tr>';
  scroll.scrollTop=top;scroll.scrollLeft=left;
 }
 function render(data){
  if(!data.ready){
   el('leaders-status').textContent='Проверенный снимок торговли пока не готов. Это не означает отсутствие покупок и продаж.';
   el('leaders-content').hidden=true;el('leaders-excluded').hidden=true;return;
  }
  el('leaders-content').hidden=false;
  const snapshot=data.snapshot,stale=data.status.error||data.now-snapshot.checked_at>180;
  el('leaders-status').textContent='Финальный снимок #'+count(snapshot.height)+' · '+date(snapshot.ts)+' '+clock(snapshot.ts)+
   (data.coverage.complete?' · история без пропусков':' · история неполная, догружаем '+count(data.coverage.missing)+' блоков')+
   (stale?' · свежие данные временно недоступны, показан сохранённый снимок':'');
  for(const side of sides)renderTable(side,data);
  el('leaders-excluded').hidden=false;
  el('leaders-excluded').textContent='Не вошли в рейтинг: покупки '+amount(data.excluded.buy.volume)+' WGNK ('+count(data.excluded.buy.swaps)+
   ' исполнений) и продажи '+amount(data.excluded.sell.volume)+' WGNK ('+count(data.excluded.sell.swaps)+' исполнений) — адрес участника не подтверждён.';
 }
 async function refresh(){
  if(ranking.loading)return;
  const id=++ranking.request,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  ranking.loading=true;el('leaders-view').setAttribute('aria-busy','true');
  el('leaders-status').textContent=ranking.data?'Обновляем рейтинг…':'Считаем крупнейших покупателей и продавцов…';
  try{
   const response=await fetch('/api/mints/leaders',{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==ranking.request)return;
   ranking.data=data;render(data);
  }catch(error){if(id===ranking.request)el('leaders-status').textContent='Не удалось обновить рейтинг. '+
   (ranking.data?.ready?'Предыдущие данные сохранены. ':'')+(error.name==='AbortError'?'Сервер не ответил вовремя.':error.message);}
  finally{clearTimeout(timer);if(id===ranking.request){ranking.loading=false;el('leaders-view').setAttribute('aria-busy','false');}}
 }
 window.addEventListener('gonka:view',()=>{if(!el('leaders-view').hidden)refresh();});
 el('refresh').addEventListener('click',()=>{if(!el('leaders-view').hidden)refresh();});
 setInterval(()=>{if(!document.hidden&&!el('leaders-view').hidden)refresh();},20000);
 if(!el('leaders-view').hidden)refresh();
})();
