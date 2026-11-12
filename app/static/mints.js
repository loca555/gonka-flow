"use strict";
const el=id=>document.getElementById(id);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const count=n=>Number(n)>0&&Number(n)<1?"< 1":Math.trunc(Number(n)).toLocaleString("en-US");
function amount(value){
 const [whole,fraction=""]=String(value??"0").split(".");
 if(BigInt(whole)===0n&&/[1-9]/.test(fraction))return "< 1";
 return BigInt(whole).toLocaleString("en-US");
}
const compact=value=>amount(value);
// USDT per one WGNK; exact integer rounding with a decimal point, no float.
function price(value){
 if(value===null||value===undefined)return "—";
 const [whole,fraction=""]=String(value).split(".");
 const raw=BigInt(whole)*1000000000000n+BigInt(fraction.padEnd(12,"0").slice(0,12));
 return GonkaChart.formatPrice(raw);
}
const short=(v,n=8)=>v.slice(0,n)+"…"+v.slice(-6);
const date=ts=>new Date(ts*1000).toLocaleDateString("en-GB",{timeZone:"Asia/Nicosia",day:"2-digit",month:"2-digit",year:"numeric"});
const clock=ts=>new Date(ts*1000).toLocaleTimeString("en-GB",{timeZone:"Asia/Nicosia",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const txUrl=h=>"https://etherscan.io/tx/"+encodeURIComponent(h);
const blockUrl=h=>"https://etherscan.io/block/"+encodeURIComponent(h);
const state={minimum:10000,sort:"time_desc",offset:0,request:0,data:null,bridge:null};
const liveMarkup=new WeakMap(),liveCharts=new WeakMap();
function setLiveHTML(host,html){
 const previous=liveMarkup.get(host);
 if(previous?.html===html&&previous.first===host.firstChild)return;
 const scroll=host.closest(".table-container"),top=scroll?.scrollTop,left=scroll?.scrollLeft;
 host.innerHTML=html;liveMarkup.set(host,{html,first:host.firstChild});
 if(scroll){scroll.scrollTop=top;scroll.scrollLeft=left;}
}
function renderLiveChart(host,data,draw){
 const key=JSON.stringify([Math.round(host.getBoundingClientRect().width),data]);
 if(liveCharts.get(host)===key&&host.childNodes.length)return;
 draw();liveCharts.set(host,key);
}
function closeOnBackdrop(dialog){
 let start=null;
 const outside=event=>{
  const box=dialog.getBoundingClientRect();
  return event.target===dialog&&(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom);
 };
 // Both ends must be on the backdrop: selecting text or using scrollbars must not dismiss.
 dialog.addEventListener("pointerdown",event=>{
  start=dialog.open&&event.isPrimary&&event.button===0&&outside(event)?{x:event.clientX,y:event.clientY}:null;
 });
 dialog.addEventListener("pointercancel",()=>{start=null;});
 dialog.addEventListener("close",()=>{start=null;});
 dialog.addEventListener("click",event=>{
  const pressed=start;start=null;
  if(!pressed||!dialog.open||event.button!==0||event.detail===0||!outside(event)||Math.hypot(event.clientX-pressed.x,event.clientY-pressed.y)>8)return;
  event.preventDefault();event.stopPropagation();dialog.close();
 });
}
function notify(message){el("toast").textContent=message;el("toast").hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>el("toast").hidden=true,3500);}
function normalizeMintSort(sort){return ({newest:"time_desc",oldest:"time_asc",largest:"amount_desc"})[sort]||sort;}
function setTableSort(id,sort){
 const table=el(id);if(!table)return;
 table.querySelectorAll("th[data-sort]").forEach(th=>{
  const active=sort.startsWith(th.dataset.sort+"_"),ascending=sort.endsWith("_asc");
  th.setAttribute("aria-sort",active?(ascending?"ascending":"descending"):"none");
  const arrow=th.querySelector(".sort-arrow");
  if(arrow)arrow.textContent=active?(ascending?"↑":"↓"):"↕";
  const button=th.querySelector("button");
  if(button)button.title="Sort "+(active&&!ascending?"ascending":"descending");
 });
 table.dataset.order=sort;
}
function configureTableSort(id,sort,onChange){
 const table=el(id);
 table.querySelectorAll("th[data-sort]").forEach(th=>{
  const label=th.textContent;
  th.innerHTML='<button type="button" class="column-sort"><span>'+esc(label)+'</span><span class="sort-arrow" aria-hidden="true">↕</span></button>';
  th.querySelector("button").addEventListener("click",()=>{
   const current=table.dataset.order,field=th.dataset.sort;
   const direction=current.startsWith(field+"_")?(current.endsWith("_asc")?"desc":"asc"):(th.dataset.default||"desc");
   onChange(field+"_"+direction);
  });
 });
 setTableSort(id,sort);
}
function configureVolumeFilter(id,onChange){
 const form=el(id),input=form.querySelector("input"),reset=form.querySelector("[data-volume-reset]");
 const clear=()=>{input.value="0";reset.disabled=true;};
 form.addEventListener("submit",event=>{
  event.preventDefault();
  if(!form.reportValidity())return;
  const minimum=Number(input.value||0);
  input.value=String(minimum);reset.disabled=minimum===0;
  onChange(minimum);
 });
 reset.addEventListener("click",()=>{clear();onChange(0);});
 return clear;
}
function showView(){
 if(location.hash==="#mints")history.replaceState(null,"",location.pathname+"#bridge");
 const otc=location.hash==="#otc"&&document.body.dataset.otcEnabled==="true"&&["localhost","127.0.0.1"].includes(location.hostname);
 const view=otc?"otc":location.hash==="#holders"?"holders":location.hash==="#leaders"?"leaders":location.hash==="#minters"?"minters":["#bridge","#method"].includes(location.hash)?"bridge":"trading";
 document.body.classList.toggle("otc-active",otc);
 const panels={bridge:"mints-view",trading:"trading-view",minters:"minters-view",leaders:"leaders-view",holders:"holders-view",otc:"otc-view"};
 Object.entries(panels).forEach(([name,id])=>{el(id).hidden=name!==view;});
 document.querySelectorAll("[data-view]").forEach(link=>{
  const active=link.dataset.view===view;
  link.classList.toggle("nav-active",active);
  if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");
 });
 if(view==="bridge"&&state.data)renderChart(state.data);
 if(location.hash==="#method")el("method").scrollIntoView({block:"start"});
 else if(state.view!==view)window.scrollTo(0,0);
 state.view=view;
 window.dispatchEvent(new Event("gonka:view"));
}
function renderCoverage(d){
 GonkaSync.update("mints",d);
 el("last-update").textContent="Server response "+clock(d.now)+" · read only";
 el("contract-link").href="https://etherscan.io/address/"+d.contract;
}
function renderMetrics(d){
 const known=d.coverage.complete||d.all_summary.events>0;
 [["metric-amount",d.summary.amount,true],["metric-txs",d.summary.transactions,false],
  ["metric-recipients",d.summary.recipients,false],["metric-largest",d.summary.largest,true]].forEach(([id,value,isAmount])=>{
   el(id).textContent=known?(isAmount?amount(value):count(value)):"—";
  });
 el("metric-events").textContent=count(d.summary.events)+" mint events";
}
function renderChart(d){
 if(el("mints-view").hidden)return;
 // 2026-06-09: deployment-wave drop of 3,268,769 WGNK; as a single bar it
 // dwarfs the daily rhythm, so it is ignored on this chart by request.
 const anomaly="2026-06-09";
 const daily=d.daily.filter(x=>x.date!==anomaly);
 const chart=el("mint-chart"),options={points:daily.map(x=>({date:x.date,raw:x.amount_raw,events:x.events,price_raw:x.price_raw,weight:x.weight,tokens:x.tokens})),
  complete:d.coverage.complete,type:"bars",title:"WGNK mints by day",countLabel:"Mints",
  legend:[{color:"var(--accent)",label:"WGNK mints",bar:true},
   {color:"#f2801e",label:"7-day average"},
   {color:"#4db8ff",label:"Price, USDT"},
   {color:"#9b59b6",label:"Network weight"},
   {color:"#d63384",label:"AI tokens per epoch (~24 h)"}]};
 renderLiveChart(chart,options,()=>GonkaChart.render(chart,options));
 const heading=chart.closest('article').querySelector('.panel-title');
 if(!el('download-mint-chart')){
  const button=document.createElement('button');button.id='download-mint-chart';button.type='button';
  button.title='Download chart PNG';button.setAttribute('aria-label',button.title);
  button.innerHTML='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></svg>';
  button.addEventListener('click',async()=>{button.disabled=true;try{await downloadMintChart();}catch(error){notify('Failed to save the chart: '+error.message);}finally{button.disabled=false;}});
  heading.append(button);
 }
}
async function downloadMintChart(){
 const host=el('mint-chart'),source=host.querySelector('svg'),clone=source.cloneNode(true);
 const originals=[source,...source.querySelectorAll('*')],copies=[clone,...clone.querySelectorAll('*')];
 originals.forEach((node,i)=>{
  const style=getComputedStyle(node);
  for(const key of ['fill','stroke','stroke-width','stroke-dasharray','opacity','font-family','font-size','font-weight','visibility'])copies[i].style.setProperty(key,style.getPropertyValue(key));
 });
 clone.querySelector('.chart-cursor')?.remove();clone.querySelector('.chart-hit')?.remove();
 const {width,height}=source.viewBox.baseVal;
 clone.setAttribute('xmlns','http://www.w3.org/2000/svg');clone.setAttribute('width',width);clone.setAttribute('height',height);
 const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d'),header=100;
 canvas.width=width*2;canvas.height=(height+header)*2;ctx.scale(2,2);
 const theme=getComputedStyle(host);ctx.fillStyle=theme.getPropertyValue('--panel');ctx.fillRect(0,0,width,height+header);
 ctx.fillStyle=theme.getPropertyValue('--text');ctx.font='bold 20px Segoe UI';ctx.fillText('Mint rhythm · Gonka Flow',18,30);
 ctx.font='12px Segoe UI';let lx=18,ly=56;
 host.querySelectorAll('.chart-legend-item').forEach(item=>{
  const label=item.textContent,w=ctx.measureText(label).width+32;if(lx+w>width-18){lx=18;ly+=22;}
  ctx.fillStyle=getComputedStyle(item.querySelector('i')).backgroundColor;ctx.fillRect(lx,ly-8,8,8);
  ctx.fillStyle=theme.getPropertyValue('--text');ctx.fillText(label,lx+14,ly);lx+=w;
 });
 const img=new Image();img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(clone));
 await img.decode();ctx.drawImage(img,0,header,width,height);
 const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('PNG unavailable');
 const downloadUrl=URL.createObjectURL(blob),link=document.createElement('a');link.href=downloadUrl;link.download='gonka-mint-chart.png';link.click();setTimeout(()=>URL.revokeObjectURL(downloadUrl),10000);
}
function renderRecipients(d){
 setLiveHTML(el("recipient-list"),d.recipients.length?d.recipients.map((r,i)=>
 '<div class="recipient"><span class="rank">'+String(i+1).padStart(2,"0")+'</span><div><button data-flow-address="'+esc(r.address)+'" title="'+esc(r.address)+'">'+esc(short(r.address,10))+'</button><small>'+count(r.events)+' mints</small></div><div class="numeric"><strong>'+esc(amount(r.amount))+'</strong><small>WGNK received</small></div></div>').join(""):'<p class="empty">No minters found yet.</p>');
}
function minterSalesLabel(e){
 if(e.kind==="bridge_burn")return '<small>Burn address</small>';
 const totals=e.minter_totals;
 if(!totals)return '<small class="minter-sales-summary" title="Sales and mint statistics for this address are not available yet">Sales / mints —</small>';
 const label="Dumped "+amount(totals.sold)+" of "+amount(totals.minted)+" WGNK";
 const hint="Over the whole address history up to the final snapshot, regardless of the date of this mint and the feed filters. Dumped — confirmed sales in the two tracked Uniswap V3 pools; of — total received from mints. Sales may include bought or transferred-in WGNK and exceed the mints. Specific token lots are not tracked.";
 return '<small class="minter-sales-summary" data-sold-raw="'+esc(totals.sold_raw)+'" data-minted-raw="'+esc(totals.minted_raw)+'" tabindex="0" title="'+esc(hint)+'">'+esc(label)+'</small>';
}
function renderRows(d){
 el("bridge-note").textContent=!d.ready?"The reconciled bridge snapshot is not ready yet. This does not mean there are no events.":
  "Mints and burns "+(d.minimum?"from "+count(d.minimum)+" WGNK":"of any size")+" · final snapshot #"+count(d.snapshot.height)+
  (d.coverage.complete?" · history without gaps":" · loading "+count(d.coverage.missing)+" blocks");
 if(!d.ready){el("prev").disabled=true;el("next").disabled=true;return;}
 el("row-count").textContent=count(d.total);
 setTableSort("mint-table",normalizeMintSort(d.sort));
 setLiveHTML(el("mint-rows"),d.items.length?d.items.map(e=>{
  const burn=e.kind==="bridge_burn";
  return '<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+(burn?"sell":"buy")+'">'+(burn?"Burn":"Mint")+'</span></td><td><button class="address-button" data-flow-address="'+esc(e.address)+'" title="'+esc(e.address)+'">'+esc(short(e.address,10))+'</button>'+minterSalesLabel(e)+'</td><td class="numeric"><span class="amount-value">'+esc(amount(e.amount))+'</span><small>WGNK</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>log #'+e.log_index+'</small></td><td><span class="final-badge">✓ Final</span><small>#'+count(e.height)+'</small></td><td><button class="detail-button" data-tx="'+esc(e.tx_hash)+'" data-log="'+e.log_index+'" aria-label="Event details '+esc(short(e.tx_hash))+'">↗</button></td></tr>';
 }).join(""):'<tr><td colspan="7" class="empty">'+(d.minimum?"No bridge events with volume from "+count(d.minimum)+" WGNK found. Lower the threshold or reset the filter.":"No bridge events in the confirmed history yet.")+'</td></tr>');
 el("prev").disabled=d.offset===0;el("next").disabled=!d.has_more;
 el("page-info").textContent=d.total?count(d.offset+1)+"–"+count(Math.min(d.offset+d.limit,d.total))+" of "+count(d.total):"0 events";
 el("csv-filtered").href="/api/mints/bridge/export.csv?"+new URLSearchParams({minimum:d.minimum,sort:d.sort});
}
async function refresh(reset=false){
 if(reset)state.offset=0;
 state.abort?.abort();const controller=state.abort=new AbortController();state.fetching=true;
 const id=++state.request,timeout=setTimeout(()=>controller.abort(),30000);
 try{
  const get=async url=>{const r=await fetch(url,{signal:controller.signal});if(!r.ok)throw new Error("No fresh response (HTTP "+r.status+").");return r.json();};
  const [d,bridge]=await Promise.all([
   get("/api/mints?minimum=0&limit=1"),
   get("/api/mints/bridge?"+new URLSearchParams({minimum:state.minimum,sort:state.sort,offset:state.offset,limit:50}))
  ]);
  if(id!==state.request)return;
  renderCoverage(d);
  if(state.bridge?.ready&&!bridge.ready){el("error-banner").textContent="The service is restoring the snapshot. The last verified data is kept; will retry automatically.";el("error-banner").hidden=false;return;}
  state.data=d;state.bridge=bridge;el("error-banner").hidden=true;
  renderMetrics(d);renderChart(d);renderRecipients(d);renderRows(bridge);
 }catch(e){if(id===state.request){
  el("error-banner").textContent=(e.name==="AbortError"?"The server did not respond in time.":e.message)+" Previous data is kept.";
  el("error-banner").hidden=false;GonkaSync.fail("mints");
 }}
 finally{clearTimeout(timeout);if(id===state.request)state.fetching=false;}
}
function details(tx,index){
 const e=state.bridge?.items.find(e=>e.tx_hash===tx&&e.log_index===index);if(!e)return;
 const burn=e.kind==="bridge_burn";
 el("mint-detail").innerHTML='<div class="detail-amount">'+esc(amount(e.amount))+' <small>WGNK</small></div><p class="detail-notice">'+
  (burn?"WGNK burn. Not a sale. The recipient and the GNK credit status are in the address history, on the Gonka tab.":"WGNK mint. Not a purchase. Matching with Gonka is in the address history.")+
  '<br>✓ Final event · '+date(e.ts)+" "+clock(e.ts)+'</p><dl><dt>'+(burn?"Burn address":"Minter")+'</dt><dd class="mono">'+esc(e.address)+'</dd><dt>Transaction / event index</dt><dd class="mono">'+esc(e.tx_hash)+' / '+e.log_index+'</dd><dt>Ethereum block</dt><dd>#'+count(e.height)+'</dd><dt>Block hash</dt><dd class="mono">'+esc(e.block_hash)+'</dd>'+
  (e.request_id?'<dt>Request ID</dt><dd class="mono">'+esc(e.request_id)+'</dd>':"")+
  '<dt>Exact amount in minimal units</dt><dd class="mono">'+esc(e.amount_raw)+'</dd></dl><div class="dialog-actions"><a href="'+txUrl(tx)+'" target="_blank" rel="noopener noreferrer">Transaction on Etherscan ↗</a><button data-copy="'+esc(e.address)+'">Copy address</button><button data-flow-address="'+esc(e.address)+'">Address history · WGNK / GNK</button></div>';
 el("mint-dialog").showModal();
}
configureVolumeFilter("bridge-volume-filter",minimum=>{state.minimum=minimum;refresh(true);});
el("prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});
el("next").addEventListener("click",()=>{state.offset+=50;refresh();});
el("close-dialog").addEventListener("click",()=>el("mint-dialog").close());
closeOnBackdrop(el("mint-dialog"));
document.addEventListener("click",async e=>{
 const copy=e.target.closest("[data-copy]");
 if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy);notify("Address copied");}catch{notify("Copying is unavailable. The address can be selected in the details.");}return;}
 const detail=e.target.closest("[data-tx]");
 if(detail)details(detail.dataset.tx,Number(detail.dataset.log));
});
history.replaceState(null,"",location.pathname+(["#method","#bridge","#trading","#mints","#minters","#leaders","#holders","#otc"].includes(location.hash)?location.hash:"#trading"));
configureTableSort("mint-table",state.sort,sort=>{state.sort=sort;refresh(true);});
window.addEventListener("hashchange",showView);
showView();refresh();
window.addEventListener("resize",()=>{if(state.data)renderChart(state.data);});
setInterval(()=>{if(!document.hidden&&!state.fetching)refresh();},15000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&!state.fetching)refresh();});

