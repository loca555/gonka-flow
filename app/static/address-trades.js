/* Address attribution means verified receipt initiator, not a person's identity. */
window.WgnkAddress=(()=>{
 const wallet={address:"",sort:"time_desc",request:0,data:null,loading:false,opener:null};
 document.body.insertAdjacentHTML("beforeend",'<dialog id="address-dialog" aria-labelledby="address-title"><div class="dialog-heading"><div><span class="eyebrow">WGNK / ALL HISTORY</span><h2 id="address-title">Address buys and sells</h2></div><button id="address-close" aria-label="Close address trades" autofocus>✕</button></div><div class="address-identity"><a id="address-link" class="mono" target="_blank" rel="noopener noreferrer"></a><button id="address-copy">Copy</button></div><p id="address-status" class="search-result" role="status" aria-live="polite"></p><div id="address-data" hidden><section id="address-metrics" class="metrics address-metrics" aria-label="Address balance and trading totals"></section><p class="flow-explanation">Only confirmed WGNK inflow/outflow of the initiator in two Uniswap V3 pools. Buys and sells are gross turnover, not profit. Transfers, mints and liquidity are not trades. CEX, OTC and other DEXes are not covered. Prices are USDT per 1 WGNK.</p><div class="table-container address-table" tabindex="0" aria-label="All address trades, scrollable list"><table id="address-trades-table"><thead><tr><th data-sort="time">Date and time</th><th data-sort="kind" data-default="asc">Trade</th><th data-sort="amount" class="numeric">WGNK volume</th><th data-sort="quote" class="numeric">USDT amount</th><th data-sort="price" class="numeric">Price per 1 WGNK, USDT</th><th data-sort="pool" data-default="asc">Pool</th><th data-sort="tx" data-default="asc">Transaction</th></tr></thead><tbody id="address-rows"></tbody></table></div><p id="address-trade-count" class="address-trade-count"></p></div><div class="address-footer"><span>All history in one list · scroll inside the table</span><button id="address-refresh">Refresh trades</button></div></dialog>');
 const dialog=el("address-dialog");
 const PAGE_SIZE=50;
 const REFRESH_INTERVAL=30000;
 let retryAt=0,retryTimer,retryRequest;
 el("address-trade-count").insertAdjacentHTML("afterend",'<div class="pagination address-pagination" aria-label="Address trade pages"><button id="address-previous" type="button">← Back</button><span id="address-page" aria-live="polite"></span><button id="address-next" type="button">Next →</button></div>');
 dialog.querySelector(".address-footer span").textContent="50 executions per page · totals and chart for all history";
 dialog.querySelector(".address-table").setAttribute("aria-label","Address trades page, scrollable list");
 function pageButtons(){
  el("address-previous").disabled=wallet.loading||Boolean(retryRequest)||!wallet.data?.ready||wallet.data.offset<=0;
  el("address-next").disabled=wallet.loading||Boolean(retryRequest)||!wallet.data?.ready||!wallet.data.has_more;
 }
 for(const [id,direction] of [["address-previous",-1],["address-next",1]]){
  el(id).addEventListener("click",()=>{if(!wallet.loading&&wallet.data?.ready)refresh(true,true,Math.max(0,wallet.data.offset+direction*PAGE_SIZE));});
 }
 el("address-metrics").insertAdjacentHTML("afterend",'<article class="panel address-chart-panel" aria-labelledby="address-chart-title"><div class="panel-title"><div><h2 id="address-chart-title">Buys, sells and balance</h2><p>By day · all address history · average price weighted by volume</p></div><div class="market-chart-legend"><span class="trade-key buy">Buys</span><span class="trade-key sell">Sells</span><span class="price-key">Balance</span></div></div><div id="address-chart" class="interactive-chart"></div><div class="chart-footer"><span>Balance is at end of day; the last day is at the snapshot.<br>Includes all WGNK transfers, mints and burns.</span><span>Hover or tap the chart</span></div></article>');
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
   host.innerHTML='<div class="empty" role="status"><p>The chart failed to load. Reload the page to load the current files.</p><button type="button">Reload page</button></div>';
   host.querySelector("button").addEventListener("click",()=>location.reload());
  }
 }
 new ResizeObserver(()=>renderChart(wallet.data)).observe(el("address-chart"));
 closeOnBackdrop(dialog);
 window.GonkaProvenance.mount(dialog);
 el("address-title").textContent="Address history";
 el("address-refresh").textContent="Refresh";
 configureTableSort("address-trades-table",wallet.sort,sort=>refresh(true,false,0,sort));
 el("address-close").addEventListener("click",()=>dialog.close());
 dialog.addEventListener("close",()=>{
  resetRequest();
  const opener=wallet.opener?.isConnected?wallet.opener:document.querySelector('#minter-rows [data-flow-address="'+wallet.address+'"]');
  opener?.focus({preventScroll:true});
 });
 el("address-refresh").addEventListener("click",()=>window.GonkaProvenance.isActive()?window.GonkaProvenance.refresh():refresh(true));
 el("address-copy").addEventListener("click",async()=>{
  try{await navigator.clipboard.writeText(wallet.address);el("address-copy").textContent="Copied";}
  catch{el("address-status").textContent="Copying is unavailable. Select the address manually.";}
 });
 function render(data,resetScroll=false){
  if(!data.ready){
   el("address-status").textContent="The reconciled snapshot is not ready yet. This does not mean there are no trades.";
   el("address-data").hidden=true;return;
  }
  const table=dialog.querySelector(".address-table"),scrollTop=resetScroll?0:table.scrollTop,dialogTop=resetScroll?0:dialog.scrollTop;
  el("address-status").textContent=count(data.total)+" executions · "+count(data.summary.transactions)+" transactions · snapshot #"+count(data.snapshot.height)+" · "+date(data.snapshot.ts)+" "+clock(data.snapshot.ts)+
   (data.coverage.complete?" · history without gaps":" · some blocks not loaded yet")+
   (data.status.error?" · fresh data is temporarily unavailable":data.now-data.snapshot.checked_at>180?" · updating data":"");
  const s=data.summary,balance=data.address_balance;
  setLiveHTML(el("address-metrics"),'<article><span>Current balance <small>WGNK</small></span><strong id="address-balance">'+(balance?esc(amount(balance.amount)):"—")+'</strong><p>'+(balance?'At verified block #'+count(balance.height)+'<br>Including all WGNK transfers':'Balance not confirmed yet')+'</p></article><article><span>Bought <small>WGNK</small></span><strong>'+esc(amount(s.bought))+'</strong><p>'+count(s.buys_count)+' executions · paid '+esc(amount(s.buy_quote))+' USDT<br>Average price per 1 WGNK: '+esc(price(s.buy_average_price))+' USDT</p></article><article><span>Sold <small>WGNK</small></span><strong>'+esc(amount(s.sold))+'</strong><p>'+count(s.sales_count)+' executions · received '+esc(amount(s.sale_quote))+' USDT<br>Average price per 1 WGNK: '+esc(price(s.sale_average_price))+' USDT</p></article>');
  setTableSort("address-trades-table",data.sort);
  setLiveHTML(el("address-rows"),data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+e.kind+'">'+(e.kind==="buy"?"Buy":"Sell")+'</span></td><td class="numeric">'+esc(amount(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote))+'</td><td class="numeric price-value">'+esc(price(e.price))+'</td><td>Uniswap V3<small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/100)+' bps</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):'<tr><td colspan="7" class="empty">No confirmed buys or sells of this address found in the tracked pools.</td></tr>');
  el("address-trade-count").textContent=count(data.total)+" executions over all history";
  el("address-page").textContent=data.total?count(data.offset+1)+"–"+count(data.offset+data.trades.length)+" of "+count(data.total):"0 executions";
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
  el("address-status").textContent="The service has temporarily limited requests. "+
   (wallet.data?.ready?"The latest data is kept. ":"")+
   "Will retry automatically in "+count(Math.ceil(delay/1000))+" s.";
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
  if(!wallet.data){el("address-status").textContent="Loading buys and sells over all history…";dialog.setAttribute("aria-busy","true");}
  try{
   const params=new URLSearchParams({sort,limit:PAGE_SIZE,offset});
   if(pageOnly&&previous?.ready){params.set("through",previous.snapshot.height);params.set("snapshot_hash",previous.snapshot.hash);params.set("as_of",previous.as_of??previous.now);}
   const response=await fetch("/api/mints/address/"+wallet.address+"?"+params,{signal:controller.signal});
   if(id!==wallet.request||!dialog.open)return;
   if(response.status===429){
    retryAt=Date.now()+Math.max(retryDelay(response.headers.get("Retry-After")),retrying?REFRESH_INTERVAL:0);
    queueRetry(request);return;
   }
   if(!response.ok)throw new Error(response.status===409?"The history snapshot has changed. Click “Refresh”.":"HTTP "+response.status);
   let data=await response.json();if(id!==wallet.request||!dialog.open)return;
   if(wallet.data?.ready&&!data.ready){el("address-status").textContent="The service is restoring the snapshot. The last verified address data is kept; will retry automatically.";return;}
   if(data.ready&&(data.offset!==offset||data.limit!==PAGE_SIZE||!Array.isArray(data.trades)||data.trades.length!==Math.max(0,Math.min(PAGE_SIZE,data.total-offset))))throw new Error("The history page was not loaded completely.");
   if(pageOnly&&data.ready){
    if(!previous?.ready||data.snapshot_height!==previous.snapshot.height||data.snapshot_hash!==previous.snapshot.hash||data.as_of!==(previous.as_of??previous.now))throw new Error("The history snapshot has changed. Click “Refresh”.");
    data={...previous,trades:data.trades,total:data.total,offset:data.offset,limit:data.limit,has_more:data.has_more,sort:data.sort};
   }
   render(data,resetScroll||!wallet.data);wallet.data=data;
  }catch(error){if(id===wallet.request&&dialog.open)el("address-status").textContent="Failed to refresh trades. "+(wallet.data?"The previous result is kept below. ":"")+"Click “Refresh”. "+(error.name==="AbortError"?"Timeout.":error.message);}
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
  el("address-data").hidden=true;el("address-copy").textContent="Copy";
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
