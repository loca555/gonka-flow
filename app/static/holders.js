/* Public snapshots, independent searches/pages for each chain. */
(()=>{
 const assets=['WGNK','GNK'],host=el('holders-content'),states={};
 const number=n=>Number(n).toLocaleString('ru-RU');
 const units=raw=>(BigInt(raw)/1000000000n).toLocaleString('ru-RU');
 const percent=bps=>bps===0?'< 0,01%':(BigInt(bps)/100n).toString()+','+(BigInt(bps)%100n).toString().padStart(2,'0')+'%';
 host.innerHTML=assets.map(asset=>'<article class="panel holders-panel" id="holders-'+asset+'"><div class="holders-heading"><div><span class="eyebrow">'+(asset==='WGNK'?'ETHEREUM':'GONKA')+'</span><h2>Холдеры '+asset+'</h2></div><span class="holders-count">— адресов</span></div><div class="holders-summary"><strong>—</strong><span>'+asset+' на адресах от 10 000</span></div><form class="holders-search"><label class="sr-only" for="holders-query-'+asset+'">Поиск адреса '+asset+'</label><input id="holders-query-'+asset+'" type="search" placeholder="Найти адрес '+asset+'" maxlength="90" pattern="[a-zA-Z0-9]*" autocomplete="off" spellcheck="false"><button type="submit">Найти</button><button type="button" data-reset disabled>Сбросить</button></form><p class="holders-status" role="status" aria-live="polite">Загружаем снимок…</p><div class="holders-progress" hidden><progress max="100" value="0" aria-label="Загрузка списка '+asset+'"></progress></div><div class="table-container holders-table" tabindex="0" aria-label="Холдеры '+asset+'"><table><thead><tr><th>№</th><th>Адрес</th><th class="numeric">Баланс '+asset+'</th><th class="numeric">Доля выпуска</th></tr></thead><tbody></tbody></table></div><div class="pagination"><button type="button" data-prev disabled>← Назад</button><span class="holders-page">—</span><button type="button" data-next disabled>Далее →</button></div></article>').join('');
 const categories={
  investors:['Инвестор','Покупки — больше 90% подтверждённого объёма WGNK за всю историю адреса.'],
  sellers:['Продавец','Продажи — больше 90% подтверждённого объёма WGNK за всю историю адреса.'],
  traders:['Трейдер','Покупки и продажи: доля каждой стороны не превышает 90% объёма WGNK.'],
  unclassified:['Без торговой истории','У адреса нет подтверждённых сделок в отслеживаемых пулах WGNK. Переводы и мост не считаются сделками.'],
  unknown:['Проверяем историю','Проверяем переводы GNK, мост и дальнейшие операции WGNK.'],
  mining_pending:['Майнинг · проверяем','Есть награды майнинга; отсутствие дальнейших расходов пока не подтверждено.'],
  miners:['Майнер','Подтверждены награды майнинга; продано строго менее 10% их объёма.'],
  module:['Модуль Gonka','Системный адрес Gonka.'],
  pool:['Пул Uniswap V3','Ликвидность пула; торговая категория адреса не присваивается.']
 };
 function badge(row){
  const key=row.pool?'pool':Object.hasOwn(categories,row.category)?row.category:'unknown';
  const [baseLabel,baseHint]=categories[key];
  const label=baseLabel+(row.estimated&&['investors','sellers','traders'].includes(key)?(row.flow_scope==='merged_balances'?' · поток':' · оценка'):'');
  const hint=row.classification_note||baseHint;
  return '<small class="holder-category holder-category-'+key+'" data-category="'+key+'" title="'+esc(hint)+'">'+esc(label)+'</small>';
 }
 function status(data){
  const stamp=data.snapshot?'Снимок #'+number(data.snapshot.height)+' · '+date(data.snapshot.ts)+' '+clock(data.snapshot.ts):'';
  const progress=data.progress?'Проверено '+number(data.progress.seen)+' из '+number(data.progress.total)+' адресов':'';
  const paused=!data.indexer_enabled?'Сборщик остановлен':data.collector?.ok===false?'Обновление задерживается, повторим автоматически':'';
  return [stamp,progress,paused].filter(Boolean).join(' · ')||(data.ready?'Снимок проверен':'Проверяем первый снимок…');
 }
 function render(asset,data){
  const s=states[asset],panel=s.panel;
  panel.querySelector('.holders-status').textContent=status(data);
  const progress=panel.querySelector('.holders-progress');progress.hidden=!data.progress;
  if(data.progress){const bar=progress.querySelector('progress');bar.max=data.progress.total||1;bar.value=data.progress.seen;}
  panel.querySelector('.holders-count').textContent=data.ready?number(data.total_holders)+' адресов':'— адресов';
  panel.querySelector('.holders-summary strong').textContent=data.ready?units(data.sum_raw):'—';
  panel.querySelector('tbody').innerHTML=!data.ready?'<tr><td colspan="4" class="empty">Первый снимок проверяется. Список появится после сверки.</td></tr>':!data.items.length?'<tr><td colspan="4" class="empty">'+(s.q?'Адрес не найден среди балансов от 10 000 '+asset+'.':'Нет адресов с балансом от 10 000 '+asset+'.')+'</td></tr>':data.items.map(row=>'<tr data-address="'+esc(row.address)+'" data-balance-raw="'+esc(row.balance_raw)+'"><td class="holder-rank">'+number(row.rank)+'</td><td class="holder-address">'+(asset==='WGNK'?'<button class="address-button" data-holder-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,10))+'</button>':'<a href="https://gonka.gg/address/'+encodeURIComponent(row.address)+'" target="_blank" rel="noopener noreferrer" title="'+esc(row.address)+'">'+esc(short(row.address,12))+' ↗</a>')+(asset==='WGNK'?badge(row):'')+'</td>'+'<td class="numeric holder-balance">'+units(row.balance_raw)+'</td><td class="numeric holder-share">'+esc(percent(row.share_bps))+'</td></tr>').join('');
  panel.querySelector('.holders-page').textContent=data.ready?(data.total?number(data.offset+1)+'–'+number(Math.min(data.offset+data.items.length,data.total))+' из '+number(data.total):'0 адресов'):'—';
  panel.querySelector('[data-prev]').disabled=!data.ready||s.offset===0;
  panel.querySelector('[data-next]').disabled=!data.ready||!data.has_more;
 }
 async function refresh(asset,reset=false){
  const s=states[asset];if(s.loading&&!reset)return;
  s.abort?.abort();const id=++s.request,controller=s.abort=new AbortController();s.loading=true;
  const timer=setTimeout(()=>controller.abort(),25000);
  s.panel.setAttribute('aria-busy','true');
  if(reset){s.data=null;render(asset,{ready:false,items:[]});}
  try{
   const response=await fetch('/api/mints/holders?'+new URLSearchParams({asset,q:s.q,offset:s.offset,limit:25}),{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==s.request)return;
   if(data.ready&&s.offset>=data.total&&s.offset){s.offset=0;s.loading=false;return refresh(asset,true);}
   if(!data.ready&&s.data?.ready){s.panel.querySelector('.holders-status').textContent='Снимок обновляется. Показаны предыдущие проверенные данные.';return;}
   s.data=data;render(asset,data);
  }catch(error){if(id===s.request){s.panel.querySelector('.holders-status').textContent='Не удалось обновить список. '+(s.data?.ready?'Показан предыдущий снимок. ':'')+'Повторим автоматически.';}}
  finally{clearTimeout(timer);if(id===s.request){s.loading=false;s.panel.setAttribute('aria-busy','false');}}
 }
 for(const asset of assets){
  const panel=el('holders-'+asset),input=panel.querySelector('input');
  const s=states[asset]={panel,q:'',offset:0,request:0,loading:false,data:null};
  panel.querySelector('form').addEventListener('submit',event=>{event.preventDefault();s.q=input.value.trim().toLowerCase();s.offset=0;panel.querySelector('[data-reset]').disabled=!s.q;refresh(asset,true);});
  panel.querySelector('[data-reset]').addEventListener('click',()=>{input.value='';s.q='';s.offset=0;panel.querySelector('[data-reset]').disabled=true;refresh(asset,true);});
  for(const [key,delta] of [['prev',-25],['next',25]])panel.querySelector('[data-'+key+']').addEventListener('click',()=>{s.offset=Math.max(0,s.offset+delta);refresh(asset,true);});
  panel.addEventListener('click',event=>{const button=event.target.closest('[data-holder-address]');if(button)WgnkAddress.open(button.dataset.holderAddress,button);});
 }
 const active=()=>location.hash==='#holders'&&!document.hidden;
 const update=()=>{if(active())assets.forEach(asset=>refresh(asset));};
 window.addEventListener('gonka:view',update);document.addEventListener('visibilitychange',update);
 el('holders-refresh').addEventListener('click',()=>assets.forEach(asset=>refresh(asset)));
 setInterval(update,30000);update();
})();
