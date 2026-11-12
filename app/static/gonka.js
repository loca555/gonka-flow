/* Cached native senders AND recipients of verified WGNK bridge operations. */
window.GonkaProvenance=(()=>{
 const native={eth:'',address:'',tab:'trades',view:'incoming',sort:'time_desc',bridgeSort:'time_desc',burnSort:'time_desc',burnsInitialized:false,data:null,history:null,request:0,historyRequest:0,loading:false};
 const addressUrl=a=>'https://gonka.gg/address/'+encodeURIComponent(a);
 const nativeTx=h=>'https://rpc.gonka.gg/api/ch/tx/'+encodeURIComponent(h);
 const names={transfer:'Transfer',reward_paid:'Reward payout',vesting_unlock:'Vesting unlock',module_transfer:'Module transfer',escrow_release:'From bridge escrow',received_unknown:'Receipt · sender not identified'};
 let dialog;
 function mount(host){
  dialog=host;
  const trading=document.createElement('section');trading.id='address-trading-panel';trading.setAttribute('role','tabpanel');trading.setAttribute('aria-labelledby','address-tab-trades');
  dialog.insertBefore(trading,el('address-status'));trading.append(el('address-status'),el('address-data'));
  trading.insertAdjacentHTML('beforebegin','<div class="address-tabs" role="tablist" aria-label="Address data"><button id="address-tab-trades" role="tab" aria-selected="true" aria-controls="address-trading-panel" data-address-tab="trades">WGNK trading</button><button id="address-tab-gonka" role="tab" aria-selected="false" aria-controls="address-gonka-panel" data-address-tab="gonka" tabindex="-1">Gonka · GNK source</button></div>');
  trading.insertAdjacentHTML('afterend','<section id="address-gonka-panel" role="tabpanel" aria-labelledby="address-tab-gonka" hidden><p id="native-status" class="search-result" role="status" aria-live="polite"></p><div id="native-content" hidden><div id="native-sources" class="native-sources" aria-label="Gonka senders"></div><div id="native-route" class="native-route"></div><p class="native-disclaimer">The link confirms a transfer through the bridge, not common ownership of the addresses or that the sender mined the GNK.</p><div class="native-heading"><div class="native-switch" role="group" aria-label="Gonka history"><button data-native-view="incoming" aria-pressed="true">Incoming GNK</button><button data-native-view="bridge" aria-pressed="false">Transfers into the bridge</button></div><a id="native-explorer" target="_blank" rel="noopener noreferrer">Address in explorer ↗</a></div><section id="native-incoming-view"><p id="native-history-status" class="search-result" role="status" aria-live="polite"></p><div id="native-history-summary" class="native-history-summary"></div><div class="table-container native-table" tabindex="0" aria-label="Incoming Gonka transfers, scrollable list"><table id="native-incoming-table"><thead><tr><th data-sort="time">Date and time</th><th data-sort="sender" data-default="asc">From</th><th data-sort="kind" data-default="asc">Receipt type</th><th data-sort="amount" class="numeric">GNK amount</th><th data-sort="tx" data-default="asc">Transaction / event</th></tr></thead><tbody id="native-incoming-rows"></tbody></table></div><p id="native-coverage-note" class="native-disclaimer"></p></section><section id="native-bridge-view" hidden><p class="native-disclaimer">One row is one verified GNK → WGNK transfer. Both transactions are linked by the bridge request ID.</p><div class="table-container native-table" tabindex="0" aria-label="Linked bridge transactions"><table id="native-bridge-table"><thead><tr><th data-sort="time">GNK sent</th><th data-sort="mint">WGNK minted</th><th data-sort="amount" class="numeric">Amount</th><th data-sort="native" data-default="asc">Gonka transaction</th><th data-sort="eth" data-default="asc">Ethereum transaction</th><th data-sort="request" data-default="asc">Request / epoch</th></tr></thead><tbody id="native-bridge-rows"></tbody></table></div></section></div></section>');
  el('native-route').insertAdjacentHTML('afterend','<section class="native-route native-balance-card" aria-label="Current GNK balance"><div><small>CURRENT ADDRESS BALANCE</small><strong id="native-balance-value" class="amount-value">—</strong><span>GNK</span></div><div><p id="native-balance-status" role="status">Waiting for balance snapshot…</p><p class="native-disclaimer">Balance on the address, not the sum of receipts. Funds in separate vesting and collateral modules are not included.</p></div></section>');
  el('address-tab-gonka').textContent='Gonka · bridge and balance';
  el('native-sources').setAttribute('aria-label','Linked Gonka addresses');
  el('native-status').insertAdjacentHTML('afterend','<details id="native-burn-archive" class="native-burn-archive" hidden><summary id="native-burn-summary">Reverse bridge · WGNK → GNK</summary><p class="native-disclaimer">Burns of this Ethereum address, including those without a prior mint. The credit is verified against the bridge record in Gonka: Ethereum block, transaction index, contract and amount. The date below is the burn date, not the time GNK was received. The credit transaction hash is not established separately.</p><div class="table-container native-table" tabindex="0" aria-label="WGNK burns and GNK recipients"><table id="native-burn-table"><thead><tr><th data-sort="time">WGNK burned</th><th data-sort="amount" class="numeric">Amount</th><th data-sort="recipient" data-default="asc">Gonka recipient</th><th data-sort="status" data-default="asc">GNK credit</th><th data-sort="tx" data-default="asc">Ethereum transaction</th></tr></thead><tbody id="native-burn-rows"></tbody></table></div></details>');
  configureTableSort('native-incoming-table',native.sort,sort=>{native.sort=sort;if(native.history)renderHistory(native.history,true);});
  configureTableSort('native-bridge-table',native.bridgeSort,sort=>{native.bridgeSort=sort;renderBridges();});
  configureTableSort('native-burn-table',native.burnSort,sort=>{native.burnSort=sort;renderBurns();});
  dialog.querySelectorAll('[data-address-tab]').forEach(button=>{
   button.addEventListener('click',()=>selectTab(button.dataset.addressTab));
   button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
    event.preventDefault();const next=event.key==='Home'?'trades':event.key==='End'?'gonka':button.dataset.addressTab==='gonka'?'trades':'gonka';selectTab(next);el('address-tab-'+next).focus();
   }});
  });
  dialog.querySelectorAll('[data-native-view]').forEach(button=>button.addEventListener('click',()=>{
   native.view=button.dataset.nativeView;renderView();
  }));
  el('address-gonka-panel').addEventListener('click',event=>{
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
  const burns=data.burns||{total:0,verified:0,pending:0,items:[]};
  el('native-status').textContent=(data.total?'GNK → WGNK: '+count(data.verified)+' of '+count(data.total)+' mints linked to Gonka. ':'GNK → WGNK: no mints found. ')+(burns.total?'WGNK → GNK: '+count(burns.total)+' burns · GNK credit confirmed for '+count(burns.verified)+(burns.pending?' · another '+count(burns.pending)+' unconfirmed':'')+'.':'WGNK → GNK: no burns found.');
  renderBurns();
  el('native-content').hidden=!data.sources.length;
  if(!data.sources.length){el('native-status').textContent+=data.total||burns.total?' Linked Gonka addresses are still being verified; this does not mean there are no GNK transfers.':' The loaded Ethereum archive has no bridge operations for this address. Trading is available in the adjacent tab.';return;}
  if(!data.sources.some(s=>s.address===native.address))native.address=data.sources[0].address;
  const source=data.sources.find(s=>s.address===native.address);
  el('native-sources').innerHTML=data.sources.map(s=>'<button data-native-address="'+esc(s.address)+'" title="'+esc(s.address)+'" aria-pressed="'+(s.address===native.address)+'"><span class="mono">'+esc(short(s.address,12))+'</span><small>'+count(s.mints)+' mints · '+count(s.burns||0)+' returns</small></button>').join('');
  el('native-route').innerHTML='<div><small>ADDRESS / GONKA</small><a class="mono" href="'+addressUrl(source.address)+'" target="_blank" rel="noopener noreferrer" title="'+esc(source.address)+'">'+esc(short(source.address,15))+' ↗</a></div><div class="native-route-amount">'+(source.mints?'<strong>'+esc(amount(source.amount))+' <small>GNK → WGNK</small></strong>':'')+(source.burns?'<strong>'+esc(amount(source.burned))+' <small>WGNK → GNK</small></strong>':'')+'<span>Confirmed links through the bridge</span></div><div><small>ADDRESS / ETHEREUM</small><span class="mono" title="'+esc(native.eth)+'">'+esc(short(native.eth,15))+'</span></div>';
  el('native-explorer').href=addressUrl(native.address);
  renderBalance(native.history?.address===native.address?native.history:null);
  renderBridges();renderView();
 }
 function renderBurns(){
  const burns=native.data?.burns,archive=el('native-burn-archive');
  archive.hidden=!burns?.total;if(!burns?.total)return;
  if(!native.burnsInitialized){archive.open=!native.data.total;native.burnsInitialized=true;}
  el('native-burn-summary').textContent='Reverse bridge · WGNK → GNK · '+count(burns.total)+' burns';
  const table=archive.querySelector('.native-table'),top=table.scrollTop;
  const [field,direction]=native.burnSort.split('_'),sign=direction==='asc'?1:-1;
  const key=e=>({time:e.eth_ts,amount:BigInt(e.amount_raw),recipient:e.gnk_address||'',status:e.status,tx:e.tx_hash})[field];
  const rows=[...burns.items].sort((a,b)=>sign*(key(a)<key(b)?-1:key(a)>key(b)?1:0));
  setTableSort('native-burn-table',native.burnSort);
  el('native-burn-rows').innerHTML=rows.map(e=>'<tr data-burn-id="'+esc(e.event_id)+'"><td>'+date(e.eth_ts)+'<small>'+clock(e.eth_ts)+'</small></td><td class="numeric amount-value" data-raw="'+esc(e.amount_raw)+'">'+esc(amount(e.amount))+'<small>WGNK → GNK</small></td><td>'+(e.gnk_address?'<button class="address-button" data-native-address="'+esc(e.gnk_address)+'" title="'+esc(e.gnk_address)+'">'+esc(short(e.gnk_address,12))+'</button>':'Not identified yet')+'</td><td>'+(e.status==='completed'?'<span class="final-badge">✓ Confirmed in Gonka</span><small><a class="tx-link" href="'+esc(e.link.evidence_url)+'" target="_blank" rel="noopener noreferrer" title="'+esc(e.link.request_id)+'">Bridge record ↗</a> · epoch '+count(e.link.epoch_id)+'</small>':'<span class="pending-badge">Verifying</span><small>'+esc(e.error||'Waiting for bridge record verification')+'</small>')+'</td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" title="'+esc(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Block #'+count(e.eth_height)+' · log #'+count(e.log_index)+'</small></td></tr>').join('');
  table.scrollTop=top;
 }
 function renderBalance(data){
  const balance=data?.address_balance,status=data?.balance_status;
  const node=el('native-balance-value');
  node.textContent=balance?amount(balance.amount):'—';
  node.dataset.raw=balance?.amount_raw||'';
  node.title=balance?balance.amount+' GNK':'';
  const stale=balance&&(status?.error||data.now-balance.checked_at>180);
  el('native-balance-status').textContent=balance?
   (stale?'Saved snapshot · ':'')+'Block #'+count(balance.height)+' · checked '+date(balance.checked_at)+' '+clock(balance.checked_at)+(status?.error?' · update temporarily unavailable':''):
   status?.error?'Balance is temporarily unavailable. A source error does not mean zero.':'Waiting for balance snapshot…';
 }
 function renderBridges(){
  if(!native.data)return;
  const [field,direction]=native.bridgeSort.split('_'),sign=direction==='asc'?1:-1;
  const key=e=>({time:e.gnk_ts,mint:e.eth_ts,amount:BigInt(e.amount_raw),native:e.gnk_tx_hash,eth:e.tx_hash,request:e.request_id})[field];
  const rows=native.data.links.filter(e=>e.gnk_address===native.address).sort((a,b)=>sign*(key(a)<key(b)?-1:key(a)>key(b)?1:0));
  setTableSort('native-bridge-table',native.bridgeSort);
  el('native-bridge-rows').innerHTML=rows.length?rows.map(e=>'<tr><td>'+date(e.gnk_ts)+'<small>'+clock(e.gnk_ts)+'</small></td><td>'+date(e.eth_ts)+'<small>'+clock(e.eth_ts)+'</small></td><td class="numeric amount-value">'+esc(amount(e.amount))+'</td><td><a class="tx-link" href="'+nativeTx(e.gnk_tx_hash)+'" title="'+esc(e.gnk_tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.gnk_tx_hash))+' ↗</a><small>Block #'+count(e.gnk_height)+'</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" title="'+esc(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Block #'+count(e.eth_height)+'</small></td><td><span class="mono" title="'+esc(e.request_id)+'">'+esc(short(e.request_id))+'</span><small>Epoch '+count(e.epoch_id)+' · confirmed</small></td></tr>').join(''):'<tr><td colspan="6" class="empty">There are no linked GNK → WGNK operations for this address pair. WGNK → GNK returns are shown in the “Reverse bridge” block.</td></tr>';
 }
 function renderHistory(data,reset=false){
  renderBalance(data);
  const table=el('native-incoming-view').querySelector('.native-table'),top=reset?0:table.scrollTop;
  const [field,direction]=native.sort.split('_'),sign=direction==='asc'?1:-1;
  const key=e=>({time:e.ts,amount:BigInt(e.amount_raw),sender:e.src,kind:e.kind,tx:e.tx_hash})[field];
  const rows=[...data.items].sort((a,b)=>sign*(key(a)<key(b)?-1:key(a)>key(b)?1:0));
  const history=data.history;
  el('native-history-status').textContent=(history.error?'Update temporarily unavailable. Showing saved records. ':history.exhausted?'Explorer feed loaded. ':'Explorer history is still loading. ')+
   (history.checked_at?'Checked '+date(history.checked_at)+' '+clock(history.checked_at)+'. ':'')+'Completeness of the whole network is not confirmed.';
  el('native-history-summary').innerHTML='<span>Loaded <strong>'+count(data.total)+'</strong> incoming events</span><span><strong>'+(data.total||history.exhausted&&!history.error?esc(amount(data.amount)):'—')+'</strong> GNK · sum of loaded receipts, not income</span><span>Source: <a href="https://rpc.gonka.gg/endpoints" target="_blank" rel="noopener noreferrer">GonkaLabs ↗</a></span>';
  setTableSort('native-incoming-table',native.sort);
  el('native-incoming-rows').innerHTML=rows.length?rows.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td>'+(e.src?'<a class="tx-link" href="'+addressUrl(e.src)+'" title="'+esc(e.src)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.src,12))+' ↗</a>':'Not identified')+(e.source_label?'<small>'+esc(e.source_label)+'</small>':'')+'</td><td><span class="native-kind '+esc(e.kind)+'">'+esc(names[e.kind]||'Receipt')+'</span>'+(e.self_transfer?'<small>Self-transfer</small>':'')+'</td><td class="numeric amount-value" data-raw="'+esc(e.amount_raw)+'">'+esc(amount(e.amount))+'</td><td><a class="tx-link" href="'+nativeTx(e.tx_hash)+'" title="'+esc(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Event #'+count(e.event_index)+' · block #'+count(e.height)+'</small></td></tr>').join(''):'<tr><td colspan="5" class="empty">'+(history.error?'Failed to load history. A source error does not mean zero receipts.':history.exhausted?'No incoming GNK found in the available explorer history.':'Receipts will appear as history loads.')+'</td></tr>';
  el('native-coverage-note').textContent=data.coverage_note+' Receipts after the transfer into the bridge are also included. Amounts are not attributed to specific sold WGNK.';
  table.scrollTop=top;
 }
 async function refreshHistory(){
  if(!native.address)return;
  const address=native.address,eth=native.eth;
  native.historyAbort?.abort();const controller=native.historyAbort=new AbortController(),id=++native.historyRequest;
  const timer=setTimeout(()=>controller.abort(),30000);
  if(!native.history){el('native-incoming-rows').innerHTML='<tr><td colspan="5" class="empty">Reading saved history…</td></tr>';el('native-history-summary').innerHTML='';el('native-coverage-note').textContent='';}
  if(!native.history)el('native-history-status').textContent='Reading incoming transfers from the database…';
  try{
   const response=await fetch('/api/mints/gonka/'+encodeURIComponent(address),{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==native.historyRequest||native.address!==address||native.eth!==eth||!dialog.open)return;
   renderHistory(data,!native.history);native.history=data;
  }catch(error){if(id===native.historyRequest&&native.address===address&&native.eth===eth&&dialog.open){
   el('native-history-status').textContent='Failed to read history. '+(native.history?'Saved records remain below. ':'')+'Click “Refresh”. '+error.message;
   if(native.history?.address_balance)el('native-balance-status').textContent+=' · no fresh response, showing saved balance';
  }}
  finally{clearTimeout(timer);}
 }
 async function refresh(){
  if(!dialog.open)return;
  native.abort?.abort();const controller=native.abort=new AbortController(),id=++native.request;
  const timer=setTimeout(()=>controller.abort(),30000);native.loading=true;
  if(!native.data)el('native-status').textContent='Reading bridge links from the database…';
  try{
   const response=await fetch('/api/mints/provenance?'+new URLSearchParams({address:native.eth}),{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==native.request||!dialog.open)return;
   native.data=data;renderOverview();
   if(data.sources.length)await refreshHistory();
  }catch(error){if(id===native.request&&dialog.open)el('native-status').textContent='Links are temporarily unavailable. '+(native.data?'Showing the saved result. ':'')+'Click “Refresh”.';}
  finally{clearTimeout(timer);if(id===native.request)native.loading=false;}
 }
 function open(eth,address=''){
  native.abort?.abort();native.historyAbort?.abort();native.request++;native.historyRequest++;
  Object.assign(native,{eth,address,tab:address?'gonka':'trades',view:'incoming',sort:'time_desc',bridgeSort:'time_desc',burnSort:'time_desc',burnsInitialized:false,data:null,history:null,loading:false});
  el('native-content').hidden=true;el('native-burn-archive').hidden=true;el('native-status').textContent='';selectTab(native.tab);
 }
 setInterval(()=>{if(dialog?.open&&!document.hidden&&native.tab==='gonka'&&!native.loading)refresh();},30000);
 document.addEventListener('visibilitychange',()=>{if(dialog?.open&&!document.hidden&&native.tab==='gonka'&&!native.loading)refresh();});
 return {mount,open,refresh,isActive:()=>native.tab==='gonka'};
})();
