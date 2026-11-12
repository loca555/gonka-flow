/* Market views are independent of the mint-size filter. Verified pool data through the latest indexed block. */
(()=>{
 const ui=id=>document.getElementById(id);
 const defaultTitle=document.title;
 let lastMarketRefresh=0;
 const flow={hours:0,minimum:10000,q:"",offset:0,side:"all",sort:"time_desc",minterSort:"sold_desc",data:null,loading:false,foreground:false,request:0};
 const pages=new Map();
 const fmt=value=>value===null||value===undefined?"—":amount(value,4);
 const addr=value=>'<button class="address-button" data-flow-address="'+esc(value)+'" title="'+esc(value)+'">'+esc(short(value,10))+'</button>';
 const holderKinds={
  investors:{label:"Investors",rule:"Buys > 90% · sells < 10%"},
  sellers:{label:"Sellers",rule:"Sells > 90% · buys < 10%"},
  traders:{label:"Traders",rule:"Buys and sells: from 10% to 90%"},
  unclassified:{label:"No trading history",rule:"No confirmed buys or sells"}
 };
 const holderView={data:null,group:null,sort:"balance_desc",opener:null};
 const share=(raw,total)=>{
  const n=BigInt(raw),d=BigInt(total);
  return !d?"—":n>0n&&n*100n<d?"< 1%":(n*100n/d).toString()+"%";
 };
 function mountHolders(){
  ui("distribution-legend").insertAdjacentHTML("afterend",'<section id="outside-holders" class="outside-holders" aria-labelledby="outside-holders-title"><div class="holder-heading"><h3 id="outside-holders-title">Outside pools · balance distribution</h3><p id="outside-holders-total"></p></div><div id="outside-holders-content" hidden><div id="holder-distribution-bar" class="holder-distribution-bar" role="img"></div><div id="holder-group-cards" class="holder-group-cards"></div><div id="holder-history-grid" class="holder-history-grid" hidden></div><p class="holder-method">Category by all-time WGNK volume: buys / (buys + sells), likewise for sells. Only confirmed trades in the two pools; transfers and the bridge are not trades. Exactly 90/10 means traders. These are nominal address categories, not established identities. The share is of the current balance outside pools, not of trading turnover.</p></div></section>');
  document.body.insertAdjacentHTML("beforeend",'<dialog id="holder-dialog" aria-labelledby="holder-dialog-title"><div class="dialog-heading"><div><span class="eyebrow">WGNK / OUTSIDE POOLS</span><h2 id="holder-dialog-title"></h2></div><button id="holder-close" aria-label="Close address list" autofocus>✕</button></div><p id="holder-dialog-summary" class="holder-dialog-summary"></p><p id="holder-dialog-rule" class="flow-explanation"></p><div class="table-container holder-address-table" tabindex="0" aria-label="All addresses in the category, scrollable list"><table id="holder-table"><thead><tr><th data-sort="address" data-default="asc">Ethereum address</th><th data-sort="balance" class="numeric">WGNK balance</th><th data-sort="share" class="numeric">Group share</th><th data-sort="bought" class="numeric">WGNK bought</th><th data-sort="sold" class="numeric">WGNK sold</th><th data-sort="buyshare" class="numeric">Buy share</th></tr></thead><tbody id="holder-rows"></tbody></table></div><p id="holder-dialog-note" class="flow-explanation"></p></dialog>');
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
   ui("holder-dialog-summary").textContent=fmt(group.balance)+" WGNK · "+share(group.balance_raw,data.total_raw)+" of WGNK outside pools · addresses: "+count(group.addresses);
   ui("holder-dialog-rule").textContent=holderKinds[group.id].rule+". Volume = WGNK bought + sold over the whole confirmed history of the two pools. Click an address to open its chart and trades.";
   ui("holder-dialog-note").textContent="Snapshot #"+count(data.height)+" · "+date(data.ts)+" "+clock(data.ts)+". All addresses with a positive balance in one list. Group share is a part of its current balance; buy share is a part of the address turnover. Percentages are shown without a fractional part; the category is calculated from exact values.";
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
  setLiveHTML(ui("holder-rows"),rows.length?rows.map(r=>'<tr data-holder-address="'+esc(r.address)+'"><td>'+addr(r.address)+'</td><td class="numeric" data-raw="'+r.balance_raw+'">'+esc(fmt(r.balance))+'</td><td class="numeric">'+esc(share(r.balance_raw,group.balance_raw))+'</td><td class="numeric">'+esc(fmt(r.bought))+'</td><td class="numeric">'+esc(fmt(r.sold))+'</td><td class="numeric">'+esc(share(r.bought_raw,(BigInt(r.bought_raw)+BigInt(r.sold_raw)).toString()))+'</td></tr>').join(""):'<tr><td colspan="6" class="empty">No addresses with a positive balance in this category yet.</td></tr>');
 }
 function renderHolders(data){
  const info=data.outside_holders;
  ui("outside-holders-content").hidden=!info?.ready;
  if(!info?.ready){ui("outside-holders-total").textContent="The balance breakdown is not confirmed yet — these are not zero values.";return;}
  ui("outside-holders-total").textContent="Shares of "+fmt(info.total)+" WGNK outside the two tracked pools · addresses: "+count(info.addresses);
  const total=BigInt(info.total_raw);
  ui("holder-distribution-bar").setAttribute("aria-label",info.groups.map(g=>holderKinds[g.id].label+": "+share(g.balance_raw,info.total_raw)).join(", "));
  setLiveHTML(ui("holder-distribution-bar"),info.groups.map(g=>'<span class="holder-'+g.id+'" style="width:'+(total?Number(BigInt(g.balance_raw)*100000n/total)/1000:0)+'%" title="'+esc(holderKinds[g.id].label+": "+fmt(g.balance)+" WGNK")+'"></span>').join(""));
  setLiveHTML(ui("holder-group-cards"),info.groups.map(g=>'<button type="button" class="holder-group holder-'+g.id+'" data-holder-group="'+g.id+'" data-balance-raw="'+g.balance_raw+'" aria-haspopup="dialog" aria-controls="holder-dialog"><span class="holder-group-name"><i></i>'+holderKinds[g.id].label+'<span aria-hidden="true">↗</span></span><strong>'+esc(fmt(g.balance))+' <small>WGNK</small></strong><span class="holder-group-share">'+esc(share(g.balance_raw,info.total_raw))+' <small>of WGNK outside pools</small></span><small class="holder-group-rule">'+esc(holderKinds[g.id].rule)+'</small><span class="holder-group-count">Addresses: '+count(g.addresses)+' <span>Open list →</span></span></button>').join(""));
  renderHolderHistory(data.holder_history,info);
 }
 // Daily balance sparkline under each holder-category card; the server
 // replays the whole verified ledger day by day (point-in-time category).
 function renderHolderHistory(history,info){
  const grid=ui("holder-history-grid");if(!grid)return;
  const ok=history&&history.ready&&Array.isArray(history.points)&&history.points.length>1&&info&&info.ready;
  grid.hidden=!ok;if(!ok)return;
  grid.innerHTML=info.groups.map(g=>'<div class="holder-history-cell holder-'+g.id+'" data-holder-spark="'+g.id+'"><svg role="img" aria-label="Balance history: '+esc(holderKinds[g.id].label)+'"></svg><div class="holder-spark-tip" hidden></div></div>').join("");
  grid.querySelectorAll("[data-holder-spark]").forEach(cell=>{
   const id=cell.dataset.holderSpark,svg=cell.querySelector("svg"),tip=cell.querySelector(".holder-spark-tip");
   const pts=history.points.map(p=>({date:p.date,raw:p[id+"_raw"],total:p.total_raw,
    v:Number(BigInt(p[id+"_raw"])/10n**6n)/1e3,n:(p.addresses||{})[id]||0}));
   const W=Math.max(140,Math.round(cell.clientWidth)||280),H=100,pad=5;
   svg.setAttribute("viewBox","0 0 "+W+" "+H);
   const vmax=Math.max.apply(null,pts.map(p=>p.v).concat([1]));
   const x=i=>pts.length===1?W/2:pad+i*(W-2*pad)/(pts.length-1);
   const y=v=>H-4-(v/vmax)*(H-18);
   const line="M"+pts.map((p,i)=>x(i).toFixed(1)+","+y(p.v).toFixed(1)).join("L");
   svg.innerHTML='<line class="spark-zero" x1="0" x2="'+W+'" y1="'+y(0).toFixed(1)+'" y2="'+y(0).toFixed(1)+'"/>'+
    '<path class="spark-area" d="'+line+'L'+x(pts.length-1).toFixed(1)+','+y(0).toFixed(1)+'L'+pad+','+y(0).toFixed(1)+'Z"/>'+
    '<path class="spark-line" d="'+line+'"/>'+
    '<circle class="spark-dot" cx="'+x(pts.length-1).toFixed(1)+'" cy="'+y(pts[pts.length-1].v).toFixed(1)+'" r="2.5"/>'+
    '<text class="spark-max" x="7" y="12">'+esc(compact(vmax))+' WGNK</text>';
   const move=event=>{
    const box=svg.getBoundingClientRect(),px=(event.clientX-box.left)*W/box.width;
    const i=Math.max(0,Math.min(pts.length-1,Math.round((px-pad)*(pts.length-1)/(W-2*pad))));
    const p=pts[i];
    tip.innerHTML='<b>'+esc(p.date.split("-").reverse().join("/"))+'</b> · '+esc(fmt(p.v))+' WGNK · '+esc(share(p.raw,p.total))+' outside pools · addresses: '+count(p.n);
    tip.hidden=false;
    tip.style.left=Math.max(4,Math.min(cell.clientWidth-tip.offsetWidth-4,x(i)*cell.clientWidth/W-tip.offsetWidth/2))+"px";
    tip.style.top="6px";
   };
   svg.addEventListener("pointermove",move);
   svg.addEventListener("pointerdown",move);
   svg.addEventListener("pointerleave",()=>{tip.hidden=true;});
  });
 }
 function mount(){
  ui("flow-content").innerHTML=
  '<article class="panel distribution-panel"><div class="panel-title"><div><h2>Where the minted WGNK are</h2><p>Total supply · regardless of mint and trade filters</p></div><span class="eyebrow">SNAPSHOT</span></div><div class="distribution-total"><strong id="distribution-total">—</strong><span>WGNK minted since the bridge was created</span></div><div id="distribution-bar" class="distribution-bar" role="img" aria-label="In pools, outside tracked pools and burned"></div><div id="distribution-legend" class="distribution-legend"></div><div id="flow-pools" class="flow-pools"></div><p class="flow-explanation">The pool balance includes WGNK from liquidity additions, swaps and fees. It does not equal the sum of LP deposits. Burned WGNK are counted on Ethereum only; completion of the reverse bridge is not verified here.</p></article>'+
  '<div class="sales-heading"><div><h2>Trading</h2><p>All addresses, not only minters · two Uniswap V3 pools · WGNK / USDT</p></div><label>Trade period <select id="sales-period"><option value="0">All history</option><option value="720">30 days</option><option value="168">7 days</option><option value="24">24 hours</option></select></label></div>'+
  '<form id="sales-search" class="sales-search"><input id="sales-query" aria-label="Trade search" placeholder="Full address or transaction hash — 0x…" maxlength="66" autocomplete="off"><button type="button" data-trade-side="all" aria-pressed="true">All trades</button><button type="button" data-trade-side="sell" aria-pressed="false">Find sells</button><button type="button" data-trade-side="buy" aria-pressed="false">Find buys</button><button type="button" id="sales-reset">Reset</button></form>'+
  '<p id="sales-result" class="search-result" role="status" aria-live="polite">Loading trades of all addresses…</p>'+
  '<section class="metrics sales-metrics" aria-label="Trade totals"><article><span><span id="flow-volume-label">Trading turnover</span> <small>WGNK</small></span><strong id="flow-sold">—</strong><dl id="flow-volume-breakdown" class="trade-breakdown" hidden></dl><p id="flow-sales-count"></p></article><article><span><span id="flow-quote-label">USDT turnover</span> <small>USDT</small></span><strong id="flow-proceeds">—</strong><dl id="flow-quote-breakdown" class="trade-breakdown" hidden></dl><p id="flow-quote-note">USDT total of buys and sells, not profit</p></article><article><span><span id="flow-price-label">Average price per 1 WGNK</span> <small>USDT</small></span><strong id="flow-price">—</strong><dl id="flow-price-breakdown" class="trade-breakdown" hidden></dl><p>Weighted by WGNK volume</p></article></section>'+
  '<p id="trade-explanation" class="flow-explanation">This is gross turnover: the same tokens can be sold repeatedly. Price = USDT out of the pool / WGNK in, without gas and possible router fees. Other DEXes, CEX and OTC trades are not counted. Regular transfers are not marked as sales.</p>'+
  '<article class="panel sales-chart-panel"><div class="panel-title"><div><h2 id="sales-chart-title">Price and volume of trading</h2><p>By day · all addresses, with trade filters applied</p></div><div class="market-chart-legend"><span class="price-key">Line · price</span><span class="trade-key buy" data-chart-side="buy">Buys</span><span class="trade-key sell" data-chart-side="sell">Sells</span></div></div><div id="sales-chart" class="interactive-chart"></div><div class="chart-footer"><span id="sales-chart-note">Price: USDT per 1 WGNK · bars: WGNK buys + sells</span><span>Hover or tap the chart</span></div></article>'+'<article class="panel sales-chart-panel liquidity-panel"><div class="panel-title"><div><h2>30 bps pool liquidity</h2><p>Trade size in USDT · solid — buy, dashed — sell</p></div><div class="liquidity-controls"><span class="market-chart-legend"><span class="price-key">Price · line</span></span><label class="liq-key liq-k2"><input type="checkbox" data-liq-level="2" checked> ±2%</label><label class="liq-key liq-k5"><input type="checkbox" data-liq-level="5"> ±5%</label><label class="liq-key liq-k10"><input type="checkbox" data-liq-level="10"> ±10%</label><label class="liq-key liq-k20"><input type="checkbox" data-liq-level="20"> ±20%</label></div></div><div id="liquidity-chart" class="interactive-chart"></div><div class="chart-footer"><span>Level ±N% is the average execution rate of a trade (0.3% pool fee included): ↑ — buying WGNK no more than +N% above the day price, ↓ — selling no less than −N% below it · lines and tooltip — trade size in USDT, shared scale on the left</span><span>Hover for values of all selected levels</span><p id="liq-live-depth" class="liq-live" hidden></p></div></article>'+
  '<div class="section-heading sales-table-heading"><div><h2><span id="trades-table-title">Trading</span> <span id="sales-total"></span></h2><p>One row is one Swap execution in a pool. Buys and sells are shown together by default.</p></div></div>'+
  '<form id="sales-volume-filter" class="volume-filter" aria-label="Trade filters"><label for="sales-side">Trades <select id="sales-side"><option value="all">Buys and sells</option><option value="sell">Sells only</option><option value="buy">Buys only</option></select></label><label for="sales-minimum">Min volume <input id="sales-minimum" type="number" min="0" max="1000000000000" step="1" value="10000" inputmode="numeric" title="Minimum volume in whole WGNK; 0 means any volume"> <span>WGNK</span></label><button type="submit">Apply</button><button type="button" data-volume-reset disabled>Reset</button></form>' +
  '<div class="table-container"><table id="trades-table"><thead><tr><th data-sort="time">Date and time</th><th data-sort="kind" data-default="asc">Trade</th><th data-sort="actor" data-default="asc">Address / initiator</th><th data-sort="pnl" data-default="desc" class="numeric" title="Address PNL: the smaller of the volumes (bought/sold) × (sell price − buy price), USDT. The open part is not counted; hidden when the result is within ±100 USDT">PNL · USDT</th><th data-sort="amount" class="numeric">WGNK volume</th><th data-sort="quote" class="numeric">USDT amount</th><th data-sort="price" class="numeric">Price per 1 WGNK, USDT</th><th data-sort="pool" data-default="asc">Pool</th><th data-sort="tx" data-default="asc">Transaction</th></tr></thead><tbody id="sales-rows"></tbody></table></div>'+
  '<p id="sales-page-status" class="search-result" role="status" aria-live="polite" hidden></p>'+
  '<div id="sales-pagination" class="pagination" role="group" aria-label="Trade pages"><button id="sales-prev" type="button" disabled>← Back</button><span id="sales-page" aria-live="polite" aria-atomic="true"></span><button id="sales-next" type="button" disabled>Next →</button></div>';
  ui("minter-report-host").innerHTML='<section class="minter-report"><h2>Minters and their sales <span id="minter-count"></span></h2><p class="flow-explanation">All history. Dates are the first and last mint of the address; dates sort by the last one. Sales may include bought or transferred-in WGNK: specific token lots are not tracked. The Gonka address is the verified sender into the bridge, not an established owner or miner.</p><p id="minter-status" class="flow-explanation"></p><div class="table-container minter-table"><table id="minter-table"><thead><tr><th data-sort="address" data-default="asc">Minter · Ethereum</th><th data-sort="native" data-default="asc">Sender · Gonka</th><th data-sort="dates">Mint dates</th><th data-sort="minted" class="numeric">Received from mints</th><th data-sort="balance" class="numeric">Balance now</th><th data-sort="sold" class="numeric">Sold by address</th><th data-sort="price" class="numeric">Average price per 1 WGNK, USDT</th><th data-sort="count">Sell executions</th></tr></thead><tbody id="minter-rows"></tbody></table></div></section>';
  mountHolders();
  function search(side=flow.side){
   const q=ui("sales-query").value.trim().toLowerCase();
   if(q&&!/^0x[0-9a-f]{1,64}$/.test(q)){
    ui("sales-result").textContent="Enter an address or hash in the 0x… format";return;
   }
   flow.q=q;flow.side=side;refresh(true);
  }
  ui("sales-search").addEventListener("submit",event=>{event.preventDefault();search();});
  document.querySelectorAll("[data-trade-side]").forEach(button=>button.addEventListener("click",()=>search(button.dataset.tradeSide)));
  configureTableSort("trades-table",flow.sort,sort=>{flow.sort=sort;refresh(true);});
  configureTableSort("minter-table",flow.minterSort,sort=>{flow.minterSort=sort;if(flow.data?.ready)renderMinters(flow.data);});
  ui("sales-period").addEventListener("change",()=>{flow.hours=Number(ui("sales-period").value);refresh(true);});
  ui("sales-side").addEventListener("change",()=>{flow.side=ui("sales-side").value;refresh(true);});
  const resetVolume=configureVolumeFilter("sales-volume-filter",minimum=>{flow.minimum=minimum;refresh(true);});
  ui("sales-reset").addEventListener("click",()=>{resetVolume();flow.minimum=0;flow.q="";flow.hours=0;flow.side="all";ui("sales-query").value="";ui("sales-period").value="0";refresh(true);});
  ui("sales-prev").addEventListener("click",()=>changePage(-1));
  ui("sales-next").addEventListener("click",()=>changePage(1));
  document.addEventListener("click",event=>{
   const address=event.target.closest("[data-flow-address]");
   if(address){
    el("mint-dialog").close();
    window.WgnkAddress.open(address.dataset.flowAddress,address,address.dataset.gnkAddress||"");
   }
  });
 }
 function changePage(direction){
  const data=flow.data;
  if(flow.foreground||!data?.ready||(direction<0?data.offset===0:!data.has_more))return;
  // Navigate from the displayed page, including after a failed request.
  flow.offset=Math.max(0,data.offset+direction*data.limit);refresh(false,false,true);
 }
 function renderPagination(message=""){
  const data=flow.data,ready=data?.ready,busy=flow.foreground;
  ui("sales-prev").disabled=busy||!ready||data.offset===0;
  ui("sales-next").disabled=busy||!ready||!data.has_more;
  ui("sales-pagination").setAttribute("aria-busy",String(busy));
  ui("sales-page").textContent=!ready?"—":data.total?count(data.offset+1)+"–"+count(Math.min(data.total,data.offset+data.limit))+" of "+count(data.total):"0 trades";
  ui("sales-page-status").textContent=message;ui("sales-page-status").hidden=!message;
 }
 function renderChart(data){
  if(ui("trading-view").hidden)return;
  ui("sales-chart-title").textContent="Price and volume of "+(data.side==="all"?"trading":data.side==="buy"?"buys":"sells");
  document.querySelectorAll("[data-chart-side]").forEach(key=>{key.hidden=data.side!=="all"&&key.dataset.chartSide!==data.side;});
  const market=ui("sales-chart");
  renderLiveChart(market,[data.daily,data.side],()=>GonkaChart.renderMarket(market,data.daily,{side:data.side}));
  renderLiquidityChart(data);
  ui("sales-chart-note").textContent="Volume-weighted price: USDT per 1 WGNK · "+(data.side==="all"?"bars: WGNK buys + sells":"volume: WGNK");
 }
 function renderBreakdown(id,visible,rows){
  const host=ui(id);host.hidden=!visible;
  host.innerHTML=visible?rows.map(r=>'<div data-trade-kind="'+r.side+'"><dt><span class="trade-key '+r.side+'">'+(r.side==="buy"?"Buys":"Sells")+'</span>'+(r.note?'<small>'+esc(r.note)+'</small>':'')+'</dt><dd><b>'+esc(r.value)+'</b> <small>'+esc(r.unit)+'</small></dd></div>').join(""):"";
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
  ui("minter-count").textContent=count(rows.length)+" addresses";
  const verified=rows.reduce((n,r)=>n+(r.gnk_verified_mints||0),0),totalMints=rows.reduce((n,r)=>n+r.mint_count,0);
  ui("minter-status").textContent="Ethereum: snapshot #"+count(data.snapshot.height)+" · "+date(data.snapshot.ts)+" "+clock(data.snapshot.ts)+(data.coverage.complete?" · history without gaps":" · some blocks not loaded yet")+". Gonka links: "+count(verified)+" / "+count(totalMints)+" mints confirmed.";
  setLiveHTML(ui("minter-rows"),rows.map(r=>'<tr><td>'+addr(r.address)+'</td><td class="minter-native">'+((r.gnk_addresses||[]).length?r.gnk_addresses.map(a=>'<button class="address-button" data-flow-address="'+esc(r.address)+'" data-gnk-address="'+esc(a)+'" title="'+esc(a)+'">'+esc(short(a,12))+'</button>').join(''):'<span class="pending-badge">Verifying</span>')+'<small>'+count(r.gnk_verified_mints||0)+' / '+count(r.mint_count)+' mints</small></td><td class="mint-dates" data-last-mint="'+r.last_mint_ts+'"><span title="'+clock(r.first_mint_ts)+'">First · '+date(r.first_mint_ts)+'</span><small title="'+clock(r.last_mint_ts)+'">Last · '+date(r.last_mint_ts)+'</small></td><td class="numeric" data-raw="'+r.minted_raw+'">'+esc(fmt(r.minted))+'</td><td class="numeric" data-balance="'+esc(r.balance)+'">'+esc(fmt(r.balance))+'</td><td class="numeric" data-raw="'+r.sales_raw+'">'+esc(fmt(r.sold))+'</td><td class="numeric price-value">'+esc(price(r.average_price))+'</td><td>'+count(r.sales_count)+'</td></tr>').join(""));
 }
 function headerPrice(value){
  const parts=/^(\d+)(?:\.(\d+))?$/.exec(String(value));
  if(!parts)return "—";
  const fraction=(parts[2]||"").padEnd(4,"0");
  const rounded=BigInt(parts[1])*1000n+BigInt(fraction.slice(0,3))+(fraction[3]>="5"?1n:0n);
  return "$"+(rounded/1000n).toLocaleString("en-US")+"."+(rounded%1000n).toString().padStart(3,"0");
 }
 function renderHeaderPrice(data,failed=false){
  const box=ui("header-price"),value=ui("header-price-value"),trade=data?.latest_trade;
  if(!box||!value)return;
  value.textContent=trade?headerPrice(trade.price):"—";  value.textContent=trade?headerPrice(trade.price):"—";
  const mcapBox=ui("header-mcap");
  if(mcapBox){
   const parts=/^(\d+)(?:\.(\d+))?$/.exec(String(trade?.price));
   const cap=raw=>{
    if(!parts||!/^\d+$/.test(String(raw)))return null;
    const fraction=parts[2]||"",numerator=BigInt(raw)*BigInt(parts[1]+fraction);
    const denominator=10n**BigInt(9+fraction.length);
    const unit=numerator>=denominator*1000000000n?1000000000n:numerator>=denominator*1000000n?1000000n:numerator>=denominator*1000n?1000n:1n;
    const digits=unit===1000000000n?2:unit===1000000n?1:0,scale=10n**BigInt(digits);
    const rounded=(numerator*scale+denominator*unit/2n)/(denominator*unit);
    const tail=(rounded%scale).toString().padStart(digits,"0").replace(/0+$/,"");
    return "$"+(rounded/scale).toLocaleString("en-US")+(tail?"."+tail:"")+(unit===1000000000n?" B":unit===1000000n?" M":unit===1000n?" K":"");
   };
   const mcap=cap(data?.gnk_circulating_raw),fdv=cap("1000000000000000000");
   mcapBox.textContent="MCAP "+(mcap||"—")+" · FDV "+(fdv||"—");
   mcapBox.title="MCAP = (Bank supply − locked account vesting − streamvesting) × WGNK/USDT price. Other reserves and collateral are not subtracted. FDV = 1 billion GNK × the same price."; if(data?.gnk_liquid_supply) mcapBox.title+=" Block #"+data.gnk_liquid_supply.height+" · checked "+new Date(data.gnk_liquid_supply.checked_at*1000).toLocaleString("en-GB")+(Date.now()/1000-data.gnk_liquid_supply.checked_at>1200?" · data is stale":"");
   if(data?.gnk_liquid_supply?.catalog_checked_at) mcapBox.title+=" Vesting account list: block #"+data.gnk_liquid_supply.catalog_height+", reconciled "+new Date(data.gnk_liquid_supply.catalog_checked_at*1000).toLocaleString("en-GB")+". New accounts are counted after a full reconciliation (once a day); MCAP is an estimate based on known locks.";
   if(!data?.gnk_liquid_supply && data?.gnk_liquid_supply_progress) mcapBox.title+=" Initial reconciliation: checked "+data.gnk_liquid_supply_progress.accounts_scanned.toLocaleString("en-US")+" accounts.";

  }
  document.title=value.textContent==="—"?defaultTitle:value.textContent+" · WGNK — Gonka Flow";
  value.setAttribute("aria-label","Price per 1 WGNK, USDT: "+value.textContent);
  const stale=failed||Boolean(data?.status?.error)||(data?.snapshot&&data.now-data.snapshot.checked_at>180);
  box.dataset.state=stale?"stale":trade?"ready":data?.ready?"empty":"loading";
  box.title=trade?"Latest "+(trade.kind==="buy"?"buy":"sell")+" in the verified snapshot · "+date(trade.ts)+" "+clock(trade.ts)+
   " · block #"+count(trade.height)+" · Swap #"+trade.log_index+". "+price(trade.price)+" USDT per 1 WGNK, without gas; two WGNK / USDT pools.":
   data?.ready?"No WGNK / USDT trades in the verified snapshot yet.":"Waiting for verified trading data.";
  if(stale)box.title+=trade?" No fresh response; showing the last available price.":" No fresh response.";
  if(trade&&!data.coverage.complete)box.title+=" History is still loading.";
 }
 function tradePnlCell(e,data){
  const raw=e.actor&&data&&data.pnl?data.pnl[e.actor]:null;
  if(raw==null)return '<td class="numeric" title="No PNL: one-sided trading or a result within ±100 USDT"></td>';
  const n=BigInt(raw),neg=n<0n,abs=neg?-n:n,value=amount((abs/10n**6n).toString());
  return '<td class="numeric" title="Address PNL: the smaller of the volumes (bought/sold) × (sell price − buy price), USDT. The open part is not counted; hidden when the result is within ±100 USDT"><span class="'+(neg?'pnl-neg':'pnl-pos')+'">'+(neg?'−':'+')+' '+esc(value)+'</span></td>';
 }
 function render(data){
  renderHeaderPrice(data);
  const summary=data.summary,buy=data.side==="buy",all=data.side==="all";
  if(!data.ready)ui("sales-result").textContent="The reconciled snapshot is not ready yet. Trades are not shown yet.";
  const warning=!data.ready?(data.status.error?"Retrying the trading data check: "+data.status.error:"Loading trading data…"):
   data.status.error?"Fresh trading data is temporarily unavailable. Showing the last verified values.":
   data.now-data.snapshot.checked_at>180?"Trading data is updating. Showing the last verified values.":"";
  ui("trade-status").textContent=warning;ui("trade-status").hidden=!warning;
  ui("flow-content").hidden=!data.ready;
  if(!data.ready)return;
  ui("sales-side").value=data.side;
  document.querySelectorAll("[data-trade-side]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.tradeSide===data.side)));
  ui("flow-volume-label").textContent=all?"Trading turnover":buy?"Bought from pools":"Sold into pools";
  ui("flow-quote-label").textContent=all?"USDT turnover":buy?"Paid into pools":"Paid out by pools";
  ui("flow-quote-note").textContent=all?"USDT total of buys and sells, not profit":buy?"USDT in for these buys":"USDT out for these sells";
  ui("flow-price-label").textContent="Average price per 1 WGNK";
  ui("flow-price-label").nextElementSibling.textContent="USDT";
  ui("trades-table-title").textContent="Trading";
  ui("trade-explanation").textContent="All addresses in the two tracked pools, not only minters. Gross turnover includes repeated trades. Prices are in USDT per 1 WGNK, without gas and possible router fees. Transfers and liquidity are not trades. Other DEXes, CEX and OTC are not counted.";
  setTableSort("trades-table",data.sort);
  ui("distribution-total").textContent=fmt(summary.minted);
  const minted=BigInt(summary.minted_raw);
  const segments=[{label:"In tracked pools",raw:summary.pooled_raw,value:summary.pooled,color:"var(--accent)"},
   {label:"Outside these pools",raw:summary.outside_raw,value:summary.outside_pools,color:"var(--flow-outside)"},
   {label:"Burned on Ethereum",raw:summary.burned_raw,value:summary.burned,color:"var(--flow-burned)"}];
  ui("distribution-bar").innerHTML=segments.map(s=>{
   const percent=minted?Number(BigInt(s.raw)*100000n/minted)/1000:0;
   return '<span style="width:'+percent+'%;background:'+s.color+'" title="'+esc(s.label+": "+amount(s.value,9)+" WGNK")+'"></span>';
  }).join("");
  setLiveHTML(ui("distribution-legend"),segments.map(s=>'<div><span><i style="background:'+s.color+'"></i>'+s.label+'</span><strong title="'+esc(amount(s.value,9))+' WGNK">'+esc(fmt(s.value))+' <small>WGNK</small></strong><small>'+(minted?count(Number(BigInt(s.raw)*10000n/minted)/100):"0")+'% of all mints</small></div>').join(""));
  renderHolders(data);
  setLiveHTML(ui("flow-pools"),data.pools.map(p=>'<a class="pool-state" href="https://etherscan.io/address/'+p.address+'" target="_blank" rel="noopener noreferrer"><span>Uniswap V3 · '+count(p.fee/100)+' bps <small>'+esc(short(p.address))+' ↗</small></span><strong>'+esc(fmt(p.balance))+' <small>WGNK</small></strong></a>').join(""));
  ui("flow-sold").textContent=compact(summary.volume);ui("flow-sold").title=amount(summary.volume,9)+" WGNK";
  ui("flow-sales-count").textContent=count(summary.swaps)+" executions · "+count(summary.transactions)+" transactions";
  ui("flow-proceeds").textContent=compact(summary.quote);ui("flow-proceeds").title=amount(summary.quote,6)+" USDT";
  ui("flow-price").textContent=price(summary.average_price);ui("flow-price").title="USDT per 1 WGNK";
  renderBreakdown("flow-volume-breakdown",all,[
   {side:"buy",value:fmt(summary.bought),unit:"WGNK",note:count(summary.buys_count)+" executions"},
   {side:"sell",value:fmt(summary.sold),unit:"WGNK",note:count(summary.sales_count)+" executions"}]);
  renderBreakdown("flow-quote-breakdown",all,[
   {side:"buy",value:fmt(summary.buy_quote),unit:"USDT",note:"Paid into pools"},
   {side:"sell",value:fmt(summary.sale_quote),unit:"USDT",note:"Received from pools"}]);
  renderBreakdown("flow-price-breakdown",all,[
   {side:"buy",value:price(summary.buy_average_price),unit:"USDT"},
   {side:"sell",value:price(summary.sale_average_price),unit:"USDT"}]);
  renderTradePage(data);
  renderMinters(data);
  renderChart(data);
 }
 function pageFields(data){
  return {trades:data.trades,total:data.total,offset:data.offset,limit:data.limit,has_more:data.has_more};
 }
 function rememberPage(data){
  if(!data.ready)return;
  pages.set(data.offset,{ready:true,...pageFields(data),snapshot_height:data.snapshot.height,snapshot_hash:data.snapshot.hash,as_of:data.now});
  if(pages.size>12)pages.delete(pages.keys().next().value);
 }
 function renderTradePage(data){
  const noun=data.side==="all"?"Trading":data.side==="buy"?"Buys":"Sells";
  ui("sales-result").textContent=noun+" · "+(data.q?data.q:"all addresses")+" · found "+count(data.total)+" executions"+
   (data.hours?" · over "+count(data.hours/24)+" days":" · all history")+
   (data.minimum?" · volume from "+count(data.minimum)+" WGNK":"");
  setTableSort("trades-table",data.sort);
  ui("sales-total").textContent=count(data.total);
  setLiveHTML(ui("sales-rows"),data.trades.length?data.trades.map(e=>'<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+e.kind+'">'+(e.kind==="buy"?"Buy":"Sell")+'</span></td><td>'+
   (e.attribution==="initiator_net"||e.attribution==="tx_net"?addr(e.actor):'<span class="mono" title="'+esc(e.actor)+'">'+esc(e.actor?short(e.actor,10):"Not identified")+'</span>')+
   '<small>'+(e.attribution==="initiator_net"?(e.kind==="buy"?"WGNK inflow confirmed":"WGNK outflow confirmed"):e.attribution==="tx_net"?(e.kind==="buy"?"WGNK recipient confirmed":"WGNK payer confirmed"):e.attribution==="initiator_only"?"Execution initiator only":"Participant not identified")+'</small></td>'+tradePnlCell(e,data)+'<td class="numeric">'+esc(fmt(e.amount))+'</td><td class="numeric">'+esc(amount(e.quote))+'</td><td class="numeric price-value">'+esc(price(e.price))+'</td><td><small>Uniswap V3</small>'+count((data.pools.find(p=>p.address===e.pool)?.fee||0)/100)+' bps</td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>Swap #'+e.idx+'</small></td></tr>').join(""):
   '<tr><td colspan="9" class="empty">No trades found for these filters in the tracked pools.</td></tr>');
 }
 async function refresh(reset=false,background=false,pageOnly=false){
  if(background&&flow.loading)return;
  if(!pageOnly)lastMarketRefresh=Date.now();
  if(reset)flow.offset=0;
  const previous=flow.data;
  flow.abort?.abort();const controller=flow.abort=new AbortController(),id=++flow.request;
  const timer=setTimeout(()=>controller.abort(),30000);flow.loading=true;flow.foreground=!background;
  let pageMessage="";
  if(!background){
   renderPagination("Loading page…");
   ui("sales-search").setAttribute("aria-busy","true");
   ui("sales-result").textContent="Searching "+(flow.side==="all"?"trades":flow.side==="buy"?"buys":"sells")+"…";
  }
  try{
   let data=pageOnly?pages.get(flow.offset):null;
   if(!data){
    const filter=pageOnly?previous:flow;
    const params={hours:filter.hours,minimum:filter.minimum,q:filter.q,offset:flow.offset,limit:25,side:filter.side,sort:filter.sort};
    if(pageOnly)Object.assign(params,{through:previous.snapshot.height,snapshot_hash:previous.snapshot.hash,as_of:previous.now});
    const response=await fetch((pageOnly?"/api/mints/trades?":"/api/mints/flows?")+new URLSearchParams(params),{signal:controller.signal});
    if(!response.ok)throw new Error("HTTP "+response.status);
    data=await response.json();
   }
   if(id!==flow.request)return;
   if(pageOnly&&data.ready&&(data.snapshot_height!==previous.snapshot.height||data.snapshot_hash!==previous.snapshot.hash||data.as_of!==previous.now))throw new Error("The trading snapshot has changed. Please reload.");
   if(!pageOnly)GonkaSync.update("market",data);
   if(flow.data?.ready&&!data.ready){
    flow.offset=flow.data.offset;
    if(!background){pageMessage="The page is not available yet. Showing previous trades; try again.";ui("sales-result").textContent=pageMessage;}
    renderHeaderPrice(flow.data,true);ui("trade-status").textContent="The service is restoring the trading snapshot. The last verified data is kept; will retry automatically.";ui("trade-status").hidden=false;return;
   }
   flow.offset=data.offset;
   if(pageOnly){flow.data={...previous,...pageFields(data)};renderTradePage(flow.data);}
   else{flow.data=data;pages.clear();render(data);}
   rememberPage(flow.data);
  }catch(error){if(id===flow.request){
   if(!pageOnly)GonkaSync.fail("market");
   flow.offset=flow.data?.offset||0;
   if(!background)pageMessage="The page failed to load. Showing previous trades; try again.";
   renderHeaderPrice(flow.data,true);
   ui("trade-status").textContent="No fresh response for pools and trades. The latest data is kept. "+(error.name==="AbortError"?"Timeout.":error.message);
   ui("trade-status").hidden=false;
   if(!background)ui("sales-result").textContent="Search failed. The previous result is kept below. Try again.";
  }}
  finally{clearTimeout(timer);if(id===flow.request){flow.loading=false;flow.foreground=false;ui("sales-search").setAttribute("aria-busy","false");renderPagination(pageMessage);}}
 }
 mount();refresh();
 const LIQ_LEVELS_KEY='gonka-liquidity-levels';
 function liquiditySelection(){
  let levels;
  try{levels=JSON.parse(localStorage.getItem(LIQ_LEVELS_KEY))||["2"];}catch{levels=["2"];}
  return levels;
 }
 function syncLiquidityControls(){
  const levels=liquiditySelection();
  document.querySelectorAll('[data-liq-level]').forEach(box=>{box.checked=levels.includes(box.dataset.liqLevel);});
 }
 function renderLiquidityChart(data){
  const host=el('liquidity-chart');if(!host)return;
  const pool=(data.pools||[]).find(p=>p.fee===3000);
  const bands=pool&&data.liquidity_bands?data.liquidity_bands[pool.address]:null;
  const levels=liquiditySelection();
  const prices=Object.fromEntries((data.daily||[]).map(d=>[d.date,d.price_raw]));
  renderLiveChart(host,[bands,levels,prices],()=>GonkaChart.renderLiquidity(host,bands,{levels,prices}));
  renderLiveDepth(data);
}
 function renderLiveDepth(data){
  const box=ui("liq-live-depth");if(!box)return;
  const live=data.depth_live;
  if(!live||!live.bands){box.hidden=true;return;}
  const money=v=>"$"+(v>=1e6?(v/1e6).toLocaleString("en-US",{maximumFractionDigits:2})+" M":v>=1e3?(v/1e3).toLocaleString("en-US",{maximumFractionDigits:1})+" K":Math.round(v).toLocaleString("en-US"));
  const rows=Object.entries(live.bands).filter(([,b])=>b&&b.up_usdt_raw&&b.down_usdt_raw).sort((a,b)=>a[0]-b[0]).map(([lvl,b])=>
   '<span title="Price after trade: ↑ $'+(+b.up_final_price).toFixed(4)+' · ↓ $'+(+b.down_final_price).toFixed(4)+'">±'+lvl+'% <b class="pnl-pos">↑ '+money(+b.up_usdt_raw/1e6)+'</b> <b class="pnl-neg">↓ '+money(+b.down_usdt_raw/1e6)+'</b></span>').join(" ");
  if(!rows){box.hidden=true;return;}
  box.innerHTML='Right now, by ticks of the 30 bps pool · price $'+(+live.price).toFixed(4)+' · average execution rate (0.3% fee included): '+rows;
  box.hidden=false;
 }
 document.querySelectorAll('[data-liq-level]').forEach(box=>box.addEventListener('change',()=>{
  const levels=[...document.querySelectorAll('[data-liq-level]')].filter(b=>b.checked).map(b=>b.dataset.liqLevel);
  try{localStorage.setItem(LIQ_LEVELS_KEY,JSON.stringify(levels));}catch{}
  if(flow.data?.ready)renderLiquidityChart(flow.data);
 }));
 syncLiquidityControls();
 window.addEventListener("resize",()=>{if(flow.data?.ready){renderChart(flow.data);renderHolders(flow.data);}});
 window.addEventListener("gonka:view",()=>{if(flow.data?.ready)renderChart(flow.data);});
 // Keep the tab quote current in the background, with fewer requests while hidden.
 setInterval(()=>{if(!flow.loading&&(!document.hidden||Date.now()-lastMarketRefresh>=30000))refresh(false,true);},10000);
 document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh(false,true);});
})();

