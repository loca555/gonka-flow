/* Public snapshots, independent searches/pages for each chain. */
(()=>{
 const assets=['WGNK','GNK'],host=el('holders-content'),states={};
 const number=n=>Number(n).toLocaleString('en-US');
 const units=raw=>(BigInt(raw)/1000000000n).toLocaleString('en-US');
 const percent=bps=>bps===0?'< 0.01%':(BigInt(bps)/100n).toString()+'.'+(BigInt(bps)%100n).toString().padStart(2,'0')+'%';
 host.innerHTML=assets.map(asset=>'<article class="panel holders-panel" id="holders-'+asset+'"><div class="holders-heading"><div><span class="eyebrow">'+(asset==='WGNK'?'ETHEREUM':'GONKA')+'</span><h2>Holders of '+asset+'</h2></div><span class="holders-count">— addresses</span></div><div class="holders-summary"><strong>—</strong><span>'+asset+' on addresses holding 10,000+</span></div><form class="holders-search"><label class="sr-only" for="holders-query-'+asset+'">Address search · '+asset+'</label><input id="holders-query-'+asset+'" type="search" placeholder="Find address in '+asset+'" maxlength="90" pattern="[a-zA-Z0-9]*" autocomplete="off" spellcheck="false"><button type="submit">Find</button><button type="button" data-reset disabled>Reset</button></form><p class="holders-status" role="status" aria-live="polite">Loading snapshot…</p><div class="holders-progress" hidden><progress max="100" value="0" aria-label="Loading list of '+asset+'"></progress></div><div class="table-container holders-table" tabindex="0" aria-label="Holders of '+asset+'"><table><thead><tr><th>#</th><th>Address</th><th class="numeric">Balance, '+asset+'</th><th class="numeric">Share of supply</th></tr></thead><tbody></tbody></table></div><div class="pagination"><button type="button" data-prev disabled>← Back</button><span class="holders-page">—</span><button type="button" data-next disabled>Next →</button></div></article>').join('');
 const categories={
  investors:['Investor','Buys are more than 90% of the confirmed WGNK volume over the whole history of the address.'],
  sellers:['Seller','Sells are more than 90% of the confirmed WGNK volume over the whole history of the address.'],
  traders:['Trader','Buys and sells: neither side exceeds 90% of WGNK volume.'],
  unclassified:['No trading history','The address has no confirmed trades in the tracked WGNK pools. Transfers and the bridge are not trades.'],
  unknown:['Checking history','Checking GNK transfers, the bridge and subsequent WGNK operations.'],
  mining_pending:['Mining · checking','Mining rewards exist; the absence of further spending is not confirmed yet.'],
  miners:['Miner','Mining rewards confirmed; strictly less than 10% of their volume was sold.'],
  module:['Gonka module','Gonka system address.'],
  pool:['Uniswap V3 pool','Pool liquidity; no trading category is assigned to the address.']
 };
 function badge(row){
  const key=row.pool?'pool':Object.hasOwn(categories,row.category)?row.category:'unknown';
  const [baseLabel,baseHint]=categories[key];
  const label=baseLabel+(row.estimated&&['investors','sellers','traders'].includes(key)?(row.flow_scope==='merged_balances'?' · flow':' · estimate'):'');
  const hint=row.classification_note||baseHint;
  return '<small class="holder-category holder-category-'+key+'" data-category="'+key+'" title="'+esc(hint)+'">'+esc(label)+'</small>';
 }
 function status(data){
  const stamp=data.snapshot?'Snapshot #'+number(data.snapshot.height)+' · '+date(data.snapshot.ts)+' '+clock(data.snapshot.ts):'';
  const progress=data.progress?'Checked '+number(data.progress.seen)+' of '+number(data.progress.total)+' addresses':'';
  const paused=!data.indexer_enabled?'Collector stopped':data.collector?.ok===false?'Update is delayed, will retry automatically':'';
  return [stamp,progress,paused].filter(Boolean).join(' · ')||(data.ready?'Snapshot verified':'Verifying the first snapshot…');
 }
 function render(asset,data){
  const s=states[asset],panel=s.panel;
  panel.querySelector('.holders-status').textContent=status(data);
  const progress=panel.querySelector('.holders-progress');progress.hidden=!data.progress;
  if(data.progress){const bar=progress.querySelector('progress');bar.max=data.progress.total||1;bar.value=data.progress.seen;}
  panel.querySelector('.holders-count').textContent=data.ready?number(data.total_holders)+' addresses':'— addresses';
  panel.querySelector('.holders-summary strong').textContent=data.ready?units(data.sum_raw):'—';
  panel.querySelector('tbody').innerHTML=!data.ready?'<tr><td colspan="4" class="empty">The first snapshot is being verified. The list will appear after reconciliation.</td></tr>':!data.items.length?'<tr><td colspan="4" class="empty">'+(s.q?'Address not found among balances of 10,000+ '+asset+'.':'No addresses with a balance of 10,000+ '+asset+'.')+'</td></tr>':data.items.map(row=>'<tr data-address="'+esc(row.address)+'" data-balance-raw="'+esc(row.balance_raw)+'"><td class="holder-rank">'+number(row.rank)+'</td><td class="holder-address">'+(asset==='WGNK'?'<button class="address-button" data-holder-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,10))+'</button>':'<a href="https://gonka.gg/address/'+encodeURIComponent(row.address)+'" target="_blank" rel="noopener noreferrer" title="'+esc(row.address)+'">'+esc(short(row.address,12))+' ↗</a>')+(asset==='WGNK'?badge(row):'')+'</td>'+'<td class="numeric holder-balance">'+units(row.balance_raw)+'</td><td class="numeric holder-share">'+esc(percent(row.share_bps))+'</td></tr>').join('');
  panel.querySelector('.holders-page').textContent=data.ready?(data.total?number(data.offset+1)+'–'+number(Math.min(data.offset+data.items.length,data.total))+' of '+number(data.total):'0 addresses'):'—';
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
   if(!data.ready&&s.data?.ready){s.panel.querySelector('.holders-status').textContent='Snapshot is updating. Showing previous verified data.';return;}
   s.data=data;render(asset,data);
  }catch(error){if(id===s.request){s.panel.querySelector('.holders-status').textContent='Failed to refresh the list. '+(s.data?.ready?'Showing the previous snapshot. ':'')+'Will retry automatically.';}}
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
