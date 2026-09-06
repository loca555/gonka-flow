"use strict";
const el=id=>document.getElementById(id);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const count=n=>Number(n)>0&&Number(n)<1?"< 1":Math.trunc(Number(n)).toLocaleString("ru-RU");
function amount(value){
 const [whole,fraction=""]=String(value??"0").split(".");
 if(BigInt(whole)===0n&&/[1-9]/.test(fraction))return "< 1";
 return BigInt(whole).toLocaleString("ru-RU");
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
const date=ts=>new Date(ts*1000).toLocaleDateString("ru-RU",{timeZone:"Asia/Nicosia",day:"2-digit",month:"2-digit",year:"numeric"});
const clock=ts=>new Date(ts*1000).toLocaleTimeString("ru-RU",{timeZone:"Asia/Nicosia",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const txUrl=h=>"https://etherscan.io/tx/"+encodeURIComponent(h);
const blockUrl=h=>"https://etherscan.io/block/"+encodeURIComponent(h);
const state={sort:"time_desc",offset:0,request:0,data:null,bridge:null};
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
  if(button)button.title="Сортировать "+(active&&!ascending?"по возрастанию":"по убыванию");
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
function showView(){
 if(location.hash==="#mints")history.replaceState(null,"",location.pathname+"#bridge");
 const view=location.hash==="#leaders"?"leaders":location.hash==="#minters"?"minters":["#bridge","#method"].includes(location.hash)?"bridge":"trading";
 const panels={bridge:"mints-view",trading:"trading-view",minters:"minters-view",leaders:"leaders-view"};
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
 const c=d.coverage,s=c.live,h=c.history;
 const stale=!s.checked_at||d.now-s.checked_at>90||!s.latest_ts||d.now-s.latest_ts>120;
 const error=s.error||h.error;
 el("status-dot").className="status-dot"+(error?" error":!stale&&c.complete?" ok":"");
 el("live-caption").textContent=!d.indexer_enabled?"Сборщик остановлен":error?"Ethereum · ожидание RPC":stale?"Ethereum · проверяем свежесть":"LIVE · Ethereum";
 el("coverage-detail").textContent=c.total?"Проверено "+count(c.covered)+" из "+count(c.total)+" блоков · до финального #"+count(c.head):"Проверяем историю Ethereum";
 el("rpc-note").hidden=!error;
 if(error)el("rpc-note").textContent=error;
 el("last-update").textContent="Ответ сервера "+clock(d.now)+" · только чтение";
 el("contract-link").href="https://etherscan.io/address/"+d.contract;
}
function renderMetrics(d){
 const known=d.coverage.complete||d.all_summary.events>0;
 [["metric-amount",d.summary.amount,true],["metric-txs",d.summary.transactions,false],
  ["metric-recipients",d.summary.recipients,false],["metric-largest",d.summary.largest,true]].forEach(([id,value,isAmount])=>{
   el(id).textContent=known?(isAmount?amount(value):count(value)):"—";
  });
 el("metric-events").textContent=count(d.summary.events)+" событий чеканки";
}
function renderChart(d){
 if(el("mints-view").hidden)return;
 GonkaChart.render(el("mint-chart"),{points:d.daily.map(x=>({date:x.date,raw:x.amount_raw,events:x.events})),
  complete:d.coverage.complete,type:state.chartType||"line",title:"Чеканка WGNK по дням",countLabel:"Выпусков"});
 el("chart-window").textContent=(d.coverage.complete?"Финальная история":"Есть пропуски истории")+" · текущий день может быть неполным";
}
function renderRecipients(d){
 el("recipient-list").innerHTML=d.recipients.length?d.recipients.map((r,i)=>
 '<div class="recipient"><span class="rank">'+String(i+1).padStart(2,"0")+'</span><div><button data-flow-address="'+esc(r.address)+'" title="'+esc(r.address)+'">'+esc(short(r.address,10))+'</button><small>'+count(r.events)+' выпусков</small></div><div class="numeric"><strong>'+esc(amount(r.amount))+'</strong><small>WGNK получено</small></div></div>').join(""):'<p class="empty">Минтеров пока не найдено.</p>';
}
function renderRows(d){
 el("bridge-note").textContent=!d.ready?"Сверенный снимок моста пока не готов. Это не означает отсутствие событий.":
  "Чеканка и сжигание любого размера · финальный снимок #"+count(d.snapshot.height)+
  (d.coverage.complete?" · история без пропусков":" · догружаем "+count(d.coverage.missing)+" блоков");
 if(!d.ready){el("prev").disabled=true;el("next").disabled=true;return;}
 el("row-count").textContent=count(d.total);
 setTableSort("mint-table",normalizeMintSort(d.sort));
 el("mint-rows").innerHTML=d.items.length?d.items.map(e=>{
  const burn=e.kind==="bridge_burn";
  return '<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><span class="trade-badge '+(burn?"sell":"buy")+'">'+(burn?"Сжигание":"Чеканка")+'</span></td><td><button class="address-button" data-flow-address="'+esc(e.address)+'" title="'+esc(e.address)+'">'+esc(short(e.address,10))+'</button><small>'+(burn?"Адрес сжигания":"Минтер")+'</small></td><td class="numeric"><span class="amount-value">'+esc(amount(e.amount))+'</span><small>WGNK</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer">'+esc(short(e.tx_hash))+' ↗</a><small>log #'+e.log_index+'</small></td><td><span class="final-badge">✓ Финальный</span><small>#'+count(e.height)+'</small></td><td><button class="detail-button" data-tx="'+esc(e.tx_hash)+'" data-log="'+e.log_index+'" aria-label="Детали события '+esc(short(e.tx_hash))+'">↗</button></td></tr>';
 }).join(""):'<tr><td colspan="7" class="empty">Событий моста в подтверждённой истории пока нет.</td></tr>';
 el("prev").disabled=d.offset===0;el("next").disabled=!d.has_more;
 el("page-info").textContent=d.total?count(d.offset+1)+"–"+count(Math.min(d.offset+d.limit,d.total))+" из "+count(d.total):"0 событий";
 el("csv-filtered").href="/api/mints/bridge/export.csv?"+new URLSearchParams({minimum:0,sort:state.sort});
}
async function refresh(reset=false){
 if(reset)state.offset=0;
 state.abort?.abort();const controller=state.abort=new AbortController();state.fetching=true;
 const id=++state.request,timeout=setTimeout(()=>controller.abort(),30000);
 el("refresh").disabled=true;
 try{
  const get=async url=>{const r=await fetch(url,{signal:controller.signal});if(!r.ok)throw new Error("Нет свежего ответа (HTTP "+r.status+").");return r.json();};
  const [d,bridge]=await Promise.all([
   get("/api/mints?minimum=0&limit=1"),
   get("/api/mints/bridge?"+new URLSearchParams({minimum:0,sort:state.sort,offset:state.offset,limit:50}))
  ]);
  if(id!==state.request)return;
  state.data=d;state.bridge=bridge;el("error-banner").hidden=true;
  renderCoverage(d);renderMetrics(d);renderChart(d);renderRecipients(d);renderRows(bridge);
 }catch(e){if(id===state.request){
  el("error-banner").textContent=(e.name==="AbortError"?"Сервер не ответил вовремя.":e.message)+" Предыдущие данные сохранены.";
  el("error-banner").hidden=false;el("live-caption").textContent="Нет свежего ответа сервера";el("status-dot").className="status-dot error";
 }}
 finally{clearTimeout(timeout);if(id===state.request){el("refresh").disabled=false;state.fetching=false;}}
}
function details(tx,index){
 const e=state.bridge?.items.find(e=>e.tx_hash===tx&&e.log_index===index);if(!e)return;
 const burn=e.kind==="bridge_burn";
 el("mint-detail").innerHTML='<div class="detail-amount">'+esc(amount(e.amount))+' <small>WGNK</small></div><p class="detail-notice">'+
  (burn?"Сжигание WGNK. Не является продажей. Получатель и статус зачисления GNK — в истории адреса, на вкладке Gonka.":"Чеканка WGNK. Не является покупкой. Сопоставление с Gonka — в истории адреса.")+
  '<br>✓ Финальное событие · '+date(e.ts)+" "+clock(e.ts)+'</p><dl><dt>'+(burn?"Адрес сжигания":"Минтер")+'</dt><dd class="mono">'+esc(e.address)+'</dd><dt>Транзакция / индекс события</dt><dd class="mono">'+esc(e.tx_hash)+' / '+e.log_index+'</dd><dt>Блок Ethereum</dt><dd>#'+count(e.height)+'</dd><dt>Хеш блока</dt><dd class="mono">'+esc(e.block_hash)+'</dd>'+
  (e.request_id?'<dt>Request ID</dt><dd class="mono">'+esc(e.request_id)+'</dd>':"")+
  '<dt>Точное количество в минимальных единицах</dt><dd class="mono">'+esc(e.amount_raw)+'</dd></dl><div class="dialog-actions"><a href="'+txUrl(tx)+'" target="_blank" rel="noopener noreferrer">Транзакция в Etherscan ↗</a><button data-copy="'+esc(e.address)+'">Копировать адрес</button><button data-flow-address="'+esc(e.address)+'">История адреса · WGNK / GNK</button></div>';
 el("mint-dialog").showModal();
}
el("prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});
el("next").addEventListener("click",()=>{state.offset+=50;refresh();});
el("refresh").addEventListener("click",()=>refresh());
el("close-dialog").addEventListener("click",()=>el("mint-dialog").close());
closeOnBackdrop(el("mint-dialog"));
document.addEventListener("click",async e=>{
 const copy=e.target.closest("[data-copy]");
 if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy);notify("Адрес скопирован");}catch{notify("Копирование недоступно. Адрес можно выделить в деталях.");}return;}
 const detail=e.target.closest("[data-tx]");
 if(detail)details(detail.dataset.tx,Number(detail.dataset.log));
});
history.replaceState(null,"",location.pathname+(["#method","#bridge","#trading","#mints","#minters","#leaders"].includes(location.hash)?location.hash:"#trading"));
document.querySelectorAll("[data-chart-type]").forEach(button=>button.addEventListener("click",()=>{
 state.chartType=button.dataset.chartType;
 document.querySelectorAll("[data-chart-type]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));
 if(state.data)renderChart(state.data);
}));
configureTableSort("mint-table",state.sort,sort=>{state.sort=sort;refresh(true);});
window.addEventListener("hashchange",showView);
showView();refresh();
window.addEventListener("resize",()=>{if(state.data)renderChart(state.data);});
setInterval(()=>{if(!document.hidden&&!state.fetching)refresh();},15000);
