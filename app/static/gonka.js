/* Only cached history of native senders linked to verified WGNK mints. */
window.GonkaProvenance=(()=>{
 const native={eth:'',address:'',tab:'trades',view:'incoming',sort:'time_desc',bridgeSort:'time_desc',data:null,history:null,request:0,historyRequest:0,loading:false};
 const addressUrl=a=>'https://gonka.gg/address/'+encodeURIComponent(a);
 const nativeTx=h=>'https://rpc.gonka.gg/api/ch/tx/'+encodeURIComponent(h);
 const names={transfer:'Перевод',reward_paid:'Выплата награды',vesting_unlock:'Разблокировка вестинга',module_transfer:'Перевод модуля',escrow_release:'Из эскроу моста',received_unknown:'Поступление · отправитель не определён'};
 let dialog;
 function mount(host){
  dialog=host;
  const trading=document.createElement('section');trading.id='address-trading-panel';trading.setAttribute('role','tabpanel');trading.setAttribute('aria-labelledby','address-tab-trades');
  dialog.insertBefore(trading,el('address-status'));trading.append(el('address-status'),el('address-data'));
  trading.insertAdjacentHTML('beforebegin','<div class="address-tabs" role="tablist" aria-label="Данные адреса"><button id="address-tab-trades" role="tab" aria-selected="true" aria-controls="address-trading-panel" data-address-tab="trades">Торговля WGNK</button><button id="address-tab-gonka" role="tab" aria-selected="false" aria-controls="address-gonka-panel" data-address-tab="gonka" tabindex="-1">Gonka · источник GNK</button></div>');
  trading.insertAdjacentHTML('afterend','<section id="address-gonka-panel" role="tabpanel" aria-labelledby="address-tab-gonka" hidden><p id="native-status" class="search-result" role="status" aria-live="polite"></p><div id="native-content" hidden><div id="native-sources" class="native-sources" aria-label="Отправители Gonka"></div><div id="native-route" class="native-route"></div><p class="native-disclaimer">Связь подтверждает перевод через мост, а не общую принадлежность адресов и не добычу GNK отправителем.</p><div class="native-heading"><div class="native-switch" role="group" aria-label="История Gonka"><button data-native-view="incoming" aria-pressed="true">Входящие GNK</button><button data-native-view="bridge" aria-pressed="false">Переводы в мост</button></div><a id="native-explorer" target="_blank" rel="noopener noreferrer">Адрес в эксплорере ↗</a></div><section id="native-incoming-view"><p id="native-history-status" class="search-result" role="status" aria-live="polite"></p><div id="native-history-summary" class="native-history-summary"></div><div class="table-container native-table" tabindex="0" aria-label="Входящие переводы Gonka, прокручиваемый список"><table id="native-incoming-table"><thead><tr><th data-sort="time">Дата и время</th><th data-sort="sender" data-default="asc">Откуда</th><th data-sort="kind" data-default="asc">Тип поступления</th><th data-sort="amount" class="numeric">Сумма GNK</th><th data-sort="tx" data-default="asc">Транзакция / событие</th></tr></thead><tbody id="native-incoming-rows"></tbody></table></div><p id="native-coverage-note" class="native-disclaimer"></p></section><section id="native-bridge-view" hidden><p class="native-disclaimer">Одна строка — один проверенный перевод GNK → WGNK. Обе транзакции связаны идентификатором запроса моста.</p><div class="table-container native-table" tabindex="0" aria-label="Связанные транзакции моста"><table id="native-bridge-table"><thead><tr><th data-sort="time">Отправлено GNK</th><th data-sort="mint">Выпущено WGNK</th><th data-sort="amount" class="numeric">Сумма</th><th data-sort="native" data-default="asc">Транзакция Gonka</th><th data-sort="eth" data-default="asc">Транзакция Ethereum</th><th data-sort="request" data-default="asc">Запрос / эпоха</th></tr></thead><tbody id="native-bridge-rows"></tbody></table></div></section></div></section>');
  configureTableSort('native-incoming-table',native.sort,sort=>{native.sort=sort;if(native.history)renderHistory(native.history,true);});
  configureTableSort('native-bridge-table',native.bridgeSort,sort=>{native.bridgeSort=sort;renderBridges();});
  dialog.querySelectorAll('[data-address-tab]').forEach(button=>{
   button.addEventListener('click',()=>selectTab(button.dataset.addressTab));
   button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
    event.preventDefault();const next=event.key==='Home'?'trades':event.key==='End'?'gonka':button.dataset.addressTab==='gonka'?'trades':'gonka';selectTab(next);el('address-tab-'+next).focus();
   }});
  });
  dialog.querySelectorAll('[data-native-view]').forEach(button=>button.addEventListener('click',()=>{
   native.view=button.dataset.nativeView;renderView();
  }));
  el('native-sources').addEventListener('click',event=>{
   const button=event.target.closest('[data-native-address]');if(!button)return;
   native.address=button.dataset.nativeAddress;native.history=null;renderOverview();refreshHistory();
  });
  dialog.addEventListener('close',()=>{native.abort?.abort();native.historyAbort?.abort();native.request++;native.historyRequest++;native.loading=false;});
 }
 function selectTab(tab){
  native.tab=tab;
  el('address-trading-panel').hidden=tab!=='trades';el('address-gonka-panel').hidden=tab!=='gonka';
  dialog.querySelectorAll('[data-address-tab]').forEach(button=>{
   const active=button.dataset.addressTab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
  });
  if(tab==='gonka'&&!native.data&&!native.loading)refresh();
 }
 function renderView(){
  el('native-incoming-view').hidden=native.view!=='incoming';el('native-bridge-view').hidden=native.view!=='bridge';
  dialog.querySelectorAll('[data-native-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.nativeView===native.view)));
 }
 function renderOverview(){
  const data=native.data;if(!data)return;
  el('native-status').textContent='Связи моста: '+count(data.verified)+' из '+count(data.total)+' выпусков подтверждено'+(data.pending?' · ещё '+count(data.pending)+' проверяются':'')+'.';
  el('native-content').hidden=!data.sources.length;
  if(!data.sources.length){el('native-status').textContent+=data.total?' Исходные адреса ещё проверяются; это не означает отсутствия GNK-переводов.':' У этого Ethereum-адреса в архиве нет выпусков WGNK.';return;}
  if(!data.sources.some(s=>s.address===native.address))native.address=data.sources[0].address;
  const source=data.sources.find(s=>s.address===native.address);
  el('native-sources').innerHTML=data.sources.map(s=>'<button data-native-address="'+esc(s.address)+'" title="'+esc(s.address)+'" aria-pressed="'+(s.address===native.address)+'"><span class="mono">'+esc(short(s.address,12))+'</span><small>'+count(s.mints)+' выпусков · '+esc(amount(s.amount))+' GNK</small></button>').join('');
  el('native-route').innerHTML='<div><small>ОТПРАВИТЕЛЬ / GONKA</small><a class="mono" href="'+addressUrl(source.address)+'" target="_blank" rel="noopener noreferrer" title="'+esc(source.address)+'">'+esc(short(source.address,15))+' ↗</a></div><div class="native-route-amount"><strong>'+esc(amount(source.amount))+' <small>GNK → WGNK</small></strong><span>через мост · '+count(source.mints)+' выпусков</span></div><div><small>ПОЛУЧАТЕЛЬ / ETHEREUM</small><span class="mono" title="'+esc(native.eth)+'">'+esc(short(native.eth,15))+'</span></div>';
  el('native-explorer').href=addressUrl(native.address);
  renderBridges();renderView();
 }
 function renderBridges(){
  if(!native.data)return;
  const [field,direction]=native.bridgeSort.split('_'),sign=direction==='asc'?1:-1;
  const key=e=>({time:e.gnk_ts,mint:e.eth_ts,amount:BigInt(e.amount_raw),native:e.gnk_tx_hash,eth:e.tx_hash,request:e.request_id})[field];
  const rows=native.data.links.filter(e=>e.gnk_address===native.address).sort((a,b)=>sign*(key(a)<key(b)?-1:key(a)>key(b)?1:0));
  setTableSort('native-bridge-table',native.bridgeSort);
  el('native-bridge-rows').innerHTML=rows.map(e=>'<tr><td>'+date(e.gnk_ts)+'<small>'+clock(e.gnk_ts)+'</small></td><td>'+date(e.eth_ts)+'<small>'+clock(e.eth_ts)+'</small></td><td class="numeric amount-value">'+esc(amount(e.amount))+'</td><td><a class="tx-link" href="'+nativeTx(e.gnk_tx_hash)+'" title="'+esc(e.gnk_tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.gnk_tx_hash))+' ↗</a><small>Блок #'+count(e.gnk_height)+'</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" title="'+esc(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Блок #'+count(e.eth_height)+'</small></td><td><span class="mono" title="'+esc(e.request_id)+'">'+esc(short(e.request_id))+'</span><small>Эпоха '+count(e.epoch_id)+' · подтверждено</small></td></tr>').join('');
 }
 function renderHistory(data,reset=false){
  const table=el('native-incoming-view').querySelector('.native-table'),top=reset?0:table.scrollTop;
  const [field,direction]=native.sort.split('_'),sign=direction==='asc'?1:-1;
  const key=e=>({time:e.ts,amount:BigInt(e.amount_raw),sender:e.src,kind:e.kind,tx:e.tx_hash})[field];
  const rows=[...data.items].sort((a,b)=>sign*(key(a)<key(b)?-1:key(a)>key(b)?1:0));
  const history=data.history;
  el('native-history-status').textContent=(history.error?'Обновление временно недоступно. Показаны сохранённые записи. ':history.exhausted?'Лента эксплорера загружена. ':'Загрузка истории эксплорера продолжается. ')+
   (history.checked_at?'Проверено '+date(history.checked_at)+' '+clock(history.checked_at)+'. ':'')+'Полнота всей сети не подтверждена.';
  el('native-history-summary').innerHTML='<span>Загружено <strong>'+count(data.total)+'</strong> входящих событий</span><span><strong>'+(data.total||history.exhausted&&!history.error?esc(amount(data.amount)):'—')+'</strong> GNK · сумма загруженных поступлений, не доход</span><span>Источник: <a href="https://rpc.gonka.gg/endpoints" target="_blank" rel="noopener noreferrer">GonkaLabs ↗</a></span>';
  setTableSort('native-incoming-table',native.sort);
  el('native-incoming-rows').innerHTML=rows.length?rows.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td>'+(e.src?'<a class="tx-link" href="'+addressUrl(e.src)+'" title="'+esc(e.src)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.src,12))+' ↗</a>':'Не определён')+(e.source_label?'<small>'+esc(e.source_label)+'</small>':'')+'</td><td><span class="native-kind '+esc(e.kind)+'">'+esc(names[e.kind]||'Поступление')+'</span>'+(e.self_transfer?'<small>Перевод самому себе</small>':'')+'</td><td class="numeric amount-value" data-raw="'+esc(e.amount_raw)+'">'+esc(amount(e.amount))+'</td><td><a class="tx-link" href="'+nativeTx(e.tx_hash)+'" title="'+esc(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Событие #'+count(e.event_index)+' · блок #'+count(e.height)+'</small></td></tr>').join(''):'<tr><td colspan="5" class="empty">'+(history.error?'Историю не удалось загрузить. Ошибка источника не означает нулевые поступления.':history.exhausted?'В доступной истории эксплорера входящие GNK не найдены.':'Поступления появятся по мере загрузки истории.')+'</td></tr>';
  el('native-coverage-note').textContent=data.coverage_note+' Поступления после перевода в мост также включены. Суммы не приписываются конкретным проданным WGNK.';
  table.scrollTop=top;
 }
 async function refreshHistory(){
  if(!native.address)return;
  const address=native.address,eth=native.eth;
  native.historyAbort?.abort();const controller=native.historyAbort=new AbortController(),id=++native.historyRequest;
  const timer=setTimeout(()=>controller.abort(),30000);
  if(!native.history){el('native-incoming-rows').innerHTML='<tr><td colspan="5" class="empty">Читаем сохранённую историю…</td></tr>';el('native-history-summary').innerHTML='';el('native-coverage-note').textContent='';}
  el('native-history-status').textContent='Читаем входящие переводы из базы…';
  try{
   const response=await fetch('/api/mints/gonka/'+encodeURIComponent(address),{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==native.historyRequest||native.address!==address||native.eth!==eth||!dialog.open)return;
   renderHistory(data,!native.history);native.history=data;
  }catch(error){if(id===native.historyRequest&&native.address===address&&native.eth===eth&&dialog.open)el('native-history-status').textContent='Не удалось прочитать историю. '+(native.history?'Сохранённые записи остаются ниже. ':'')+'Нажмите «Обновить». '+error.message;}
  finally{clearTimeout(timer);}
 }
 async function refresh(){
  if(!dialog.open)return;
  native.abort?.abort();const controller=native.abort=new AbortController(),id=++native.request;
  const timer=setTimeout(()=>controller.abort(),30000);native.loading=true;
  if(!native.data)el('native-status').textContent='Читаем связи моста из базы…';
  try{
   const response=await fetch('/api/mints/provenance?'+new URLSearchParams({address:native.eth}),{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==native.request||!dialog.open)return;
   native.data=data;renderOverview();
   if(data.sources.length)await refreshHistory();
  }catch(error){if(id===native.request&&dialog.open)el('native-status').textContent='Связи временно недоступны. '+(native.data?'Показан сохранённый результат. ':'')+'Нажмите «Обновить».';}
  finally{clearTimeout(timer);if(id===native.request)native.loading=false;}
 }
 function open(eth,address=''){
  native.abort?.abort();native.historyAbort?.abort();native.request++;native.historyRequest++;
  Object.assign(native,{eth,address,tab:address?'gonka':'trades',view:'incoming',sort:'time_desc',bridgeSort:'time_desc',data:null,history:null,loading:false});
  el('native-content').hidden=true;el('native-status').textContent='';selectTab(native.tab);
 }
 setInterval(()=>{if(dialog?.open&&!document.hidden&&native.tab==='gonka'&&!native.loading)refresh();},30000);
 return {mount,open,refresh,isActive:()=>native.tab==='gonka'};
})();
