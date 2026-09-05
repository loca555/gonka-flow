"use strict";
const el=id=>document.getElementById(id);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const count=n=>Number(n).toLocaleString("ru-RU").replace(",",".");
function amount(value,decimals=4){
 const [whole,fraction=""]=String(value??"0").split(".");
 if(BigInt(whole)===0n&&/[1-9]/.test(fraction)&&!/[1-9]/.test(fraction.slice(0,decimals)))
  return "< 0."+"0".repeat(decimals-1)+"1";
 return BigInt(whole).toLocaleString("ru-RU")+(fraction.slice(0,decimals).replace(/0+$/,"")?"."+fraction.slice(0,decimals).replace(/0+$/,""):"");
}
function compact(value){
 const whole=BigInt(String(value).split(".")[0]);
 if(whole>=1000000n)return amount((whole/1000000n).toString()+"."+(whole%1000000n).toString().padStart(6,"0"),2)+" млн";
 return amount(value,2);
}
const short=(v,n=8)=>v.slice(0,n)+"…"+v.slice(-6);
const date=ts=>new Date(ts*1000).toLocaleDateString("ru-RU",{timeZone:"Asia/Nicosia",day:"2-digit",month:"2-digit",year:"numeric"});
const clock=ts=>new Date(ts*1000).toLocaleTimeString("ru-RU",{timeZone:"Asia/Nicosia",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const txUrl=h=>"https://etherscan.io/tx/"+encodeURIComponent(h);
const blockUrl=h=>"https://etherscan.io/block/"+encodeURIComponent(h);
const params=new URLSearchParams(location.search);
const integer=(value,fallback,max=1e12)=>value!==null&&/^\d+$/.test(value)&&Number(value)<=max?Number(value):fallback;
const state={minimum:integer(params.get("minimum"),10000),hours:integer(params.get("hours"),0,175200),
 q:(params.get("q")||"").toLowerCase(),finality:params.get("finality")==="all"?"all":"finalized",
 sort:/^(newest|oldest|largest|(time|recipient|amount|tx|status)_(asc|desc))$/.test(params.get("sort"))?params.get("sort"):"newest",offset:0,request:0,data:null};
if(!/^0x[0-9a-f]{1,64}$/.test(state.q))state.q="";
function query(includePage=true){return new URLSearchParams({minimum:state.minimum,hours:state.hours,q:state.q,
 finality:state.finality,sort:state.sort,...(includePage?{offset:state.offset,limit:50}:{})});}
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
 const minters=location.hash==="#minters";
 el("mints-view").hidden=minters;el("minters-view").hidden=!minters;
 document.querySelectorAll("[data-view]").forEach(link=>{
  const active=link.dataset.view===(minters?"minters":"mints");
  link.classList.toggle("nav-active",active);
  if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");
 });
 if(!minters&&state.data)renderChart(state.data);
 window.dispatchEvent(new Event("gonka:view"));
}

function controls(){
 el("minimum").value=state.minimum;el("period").value=state.hours;el("query").value=state.q;
 el("pending").checked=state.finality==="all";el("sort").value=({time_desc:"newest",time_asc:"oldest",amount_desc:"largest"})[state.sort]||state.sort;
 setTableSort("mint-table",normalizeMintSort(state.sort));
 document.querySelectorAll("[data-minimum]").forEach(b=>{const selected=Number(b.dataset.minimum)===state.minimum;b.classList.toggle("selected",selected);b.setAttribute("aria-pressed",String(selected));});
 el("selection-caption").textContent=(state.minimum?"От "+count(state.minimum)+" WGNK":"Все размеры")+
  " · "+(state.hours?state.hours===24?"24 часа":state.hours/24+" дней":"вся история")+
  (state.q?" · поиск "+short(state.q):"")+(state.finality==="all"?" · включая предварительные":" · только финальные");
 el("csv-filtered").href="/api/mints/export.csv?"+query(false);
 history.replaceState(null,"","/?"+query(false)+(["#method","#minters"].includes(location.hash)?location.hash:"#mints"));
}
function renderCoverage(d){
 const c=d.coverage,s=c.live,h=c.history;
 if(c.deployment){
  el("deployment-date").textContent=date(c.deployment.ts);
  el("deployment-block").textContent="Блок #"+count(c.deployment.height)+" ↗";
  el("deployment-block").href=blockUrl(c.deployment.height);
 }
 const stale=!s.checked_at||d.now-s.checked_at>90||!s.latest_ts||d.now-s.latest_ts>120;
 const error=s.error||h.error;
 el("status-dot").className="status-dot"+(error?" error":!stale&&c.complete?" ok":"");
 el("coverage-title").textContent=c.complete?"История с создания контракта загружена":c.deployment?"Догружаем историю чеканки":"Проверяем блок создания контракта";
 el("coverage-progress").value=c.total?Math.min(c.complete?100:99.99,c.covered/c.total*100):0;
 el("live-caption").textContent=!d.indexer_enabled?"Сборщик остановлен":error?"RPC: ожидание повтора":stale?"Свежие данные пока не подтверждены":"● LIVE · Ethereum";
 el("coverage-detail").textContent=c.total?"Проверено "+count(c.covered)+" из "+count(c.total)+" блоков · "+
  (c.complete?"до финального #"+count(c.head):"осталось "+count(c.missing)):"Полнота будет подтверждена по обработанным диапазонам.";
 el("archive-count").textContent=count(d.all_summary.events)+" финальных выпусков · "+count(d.all_summary.transactions)+" транзакций";
 el("rpc-note").hidden=!error;
 if(error){const retry=s.error?s:h;el("rpc-note").textContent=error+(retry.retry_at>d.now?" · повтор после "+clock(retry.retry_at):"");}
 el("last-update").textContent="Ответ сервера "+clock(d.now)+" · только чтение";
 el("pending-count").textContent=d.pending_events?"("+count(d.pending_events)+")":"";
 el("contract-link").href="https://etherscan.io/address/"+d.contract;
 el("contract-link").title=d.contract;
}
function renderMetrics(d){
 const known=d.coverage.complete||d.all_summary.events>0;
 [["metric-amount",d.summary.amount,true],["metric-txs",d.summary.transactions,false],
  ["metric-recipients",d.summary.recipients,false],["metric-largest",d.summary.largest,true]].forEach(([id,value,isAmount])=>{
   el(id).textContent=known?(isAmount?compact(value):count(value)):"—";
   el(id).title=isAmount?amount(value,9)+" WGNK":count(value);
  });
 el("metric-events").textContent=count(d.summary.events)+" событий чеканки";
}
function renderChart(d){
 GonkaChart.render(el("mint-chart"),{points:d.daily.map(x=>({date:x.date,raw:x.amount_raw,events:x.events})),
  complete:d.coverage.complete,type:state.chartType||"line",title:"Чеканка WGNK по дням",countLabel:"Выпусков"});
 el("chart-window").textContent=(d.coverage.complete?"Финальная история":"Только загруженные события · есть пропуски")+" · текущий день может быть неполным";
}
function renderRecipients(d){
 el("recipient-list").innerHTML=d.recipients.length?d.recipients.map((r,i)=>
 '<div class="recipient"><span class="rank">'+String(i+1).padStart(2,"0")+'</span><div><button data-recipient="'+esc(r.address)+'" title="'+esc(r.address)+'">'+esc(short(r.address,10))+'</button><small>'+count(r.events)+' выпусков</small></div><div class="numeric"><strong title="'+esc(amount(r.amount,9))+' WGNK">'+esc(amount(r.amount,2))+'</strong><small>WGNK получено</small></div></div>').join(""):'<p class="empty">Получателей по этим фильтрам нет.</p>';
}
function renderRows(d){
 el("row-count").textContent=count(d.total);
 el("mint-rows").innerHTML=d.items.length?d.items.map(e=>
 '<tr><td>'+date(e.ts)+'<small>'+clock(e.ts)+'</small></td><td><button class="address-button" data-recipient="'+esc(e.recipient)+'" title="'+esc(e.recipient)+'">'+esc(short(e.recipient,10))+'</button><small>Получатель выпуска</small></td><td class="numeric"><span class="amount-value" title="'+esc(amount(e.amount,9))+'">'+esc(amount(e.amount,4))+'</span><small>WGNK · чеканка</small></td><td><a class="tx-link" href="'+txUrl(e.tx_hash)+'" target="_blank" rel="noopener noreferrer" title="'+esc(e.tx_hash)+'">'+esc(short(e.tx_hash))+' ↗</a><small>log #'+e.log_index+'</small></td><td><span class="'+(e.finalized?"final-badge":"pending-badge")+'">'+(e.finalized?"✓ Финальный":"◌ Предварительный")+'</span><small>#'+count(e.height)+'</small></td><td><button class="detail-button" data-tx="'+esc(e.tx_hash)+'" data-log="'+e.log_index+'" aria-label="Детали выпуска '+esc(short(e.tx_hash))+'">↗</button></td></tr>').join(""):
 '<tr><td colspan="6" class="empty">Выпусков по этим фильтрам не найдено в загруженной истории.<br>Нажмите «Все выпуски» или измените поиск.</td></tr>';
 el("prev").disabled=d.offset===0;el("next").disabled=!d.has_more;
 el("page-info").textContent=d.total?count(d.offset+1)+"–"+count(Math.min(d.offset+d.limit,d.total))+" из "+count(d.total):"0 выпусков";
}
async function refresh(reset=false){
 if(reset)state.offset=0;
 controls();
 state.abort?.abort();const controller=state.abort=new AbortController();state.fetching=true;
 const id=++state.request,timeout=setTimeout(()=>controller.abort(),30000);
 el("refresh").disabled=true;
 try{
  const r=await fetch("/api/mints?"+query(),{signal:state.abort.signal});
  if(!r.ok)throw new Error(r.status===422?"Проверьте порог и поиск: нужен hex-адрес или хеш, начинающийся с 0x.":"Не удалось прочитать индекс (HTTP "+r.status+").");
  const d=await r.json();
  if(id!==state.request)return;
  state.data=d;el("error-banner").hidden=true;
  renderCoverage(d);renderMetrics(d);renderChart(d);renderRecipients(d);renderRows(d);
 }catch(e){if(id===state.request){
  el("error-banner").textContent=e.name==="AbortError"?"Сервер не ответил вовремя. Сохранённые данные не заменены нулями.":e.message;
  el("error-banner").hidden=false;el("live-caption").textContent="Нет свежего ответа сервера";
  el("status-dot").className="status-dot error";
 }}
 finally{clearTimeout(timeout);if(id===state.request){el("refresh").disabled=false;state.fetching=false;}}
}
async function details(tx,index){
 const dialog=el("mint-dialog"),detailId=state.detailRequest=(state.detailRequest||0)+1;
 el("mint-detail").innerHTML='<p class="empty">Загружаем детали…</p>';dialog.showModal();
 try{
  const r=await fetch("/api/mints/tx/"+encodeURIComponent(tx),{signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw new Error("Выпуск не найден в текущем индексе. Предварительное событие могло измениться.");
  const data=await r.json(),e=data.items.find(item=>item.log_index===index);
  if(detailId!==state.detailRequest||!dialog.open)return;
  if(!e)throw new Error("Это событие больше не найдено в индексе.");
  el("mint-detail").innerHTML='<div class="detail-amount">'+esc(amount(e.amount,9))+' <small>WGNK</small></div><p class="detail-notice">'+(e.finalized?"✓ Финальный выпуск":"◌ Предварительный выпуск — может измениться")+' · '+date(e.ts)+" "+clock(e.ts)+'<br>Это чеканка, не покупка. Сторона GNK не проверялась.</p><dl><dt>Получатель</dt><dd class="mono">'+esc(e.recipient)+'</dd><dt>Транзакция / индекс события</dt><dd class="mono">'+esc(e.tx_hash)+' / '+e.log_index+'</dd><dt>Блок Ethereum</dt><dd>#'+count(e.height)+'</dd><dt>Хеш блока</dt><dd class="mono">'+esc(e.block_hash)+'</dd><dt>Эпоха из события Ethereum</dt><dd>'+esc(e.epoch_id)+'</dd><dt>Request ID моста</dt><dd class="mono">'+esc(e.request_id)+'</dd><dt>Точное количество в минимальных единицах (9 знаков)</dt><dd class="mono">'+esc(e.amount_raw)+'</dd><dt>Источник</dt><dd>'+(e.source==="verified_local_ethereum_archive"?"Проверенный локальный архив Ethereum":"Ethereum RPC · проверка receipt и ERC-20 mint")+'</dd></dl><div class="dialog-actions"><a href="'+txUrl(tx)+'" target="_blank" rel="noopener noreferrer">Транзакция в Etherscan ↗</a><button data-copy="'+esc(e.recipient)+'">Копировать получателя</button><button data-recipient="'+esc(e.recipient)+'">Все выпуски получателю</button></div>';
 }catch(e){if(detailId===state.detailRequest&&dialog.open)el("mint-detail").innerHTML='<p class="detail-notice">'+esc(e.message)+'</p>';}
}
document.querySelectorAll("[data-minimum]").forEach(b=>b.addEventListener("click",()=>{state.minimum=Number(b.dataset.minimum);refresh(true);}));
el("minimum").addEventListener("change",()=>{if(!el("minimum").checkValidity())return el("minimum").reportValidity();state.minimum=Number(el("minimum").value);refresh(true);});
el("period").addEventListener("change",()=>{state.hours=Number(el("period").value);refresh(true);});
el("sort").addEventListener("change",()=>{state.sort=el("sort").value;refresh(true);});
el("pending").addEventListener("change",()=>{state.finality=el("pending").checked?"all":"finalized";refresh(true);});
el("search-form").addEventListener("submit",e=>{e.preventDefault();const q=el("query").value.trim().toLowerCase();if(q&&!/^0x[0-9a-f]{1,64}$/.test(q))return notify("Введите адрес или хеш в формате 0x…");state.q=q;refresh(true);});
el("reset").addEventListener("click",()=>{Object.assign(state,{minimum:10000,hours:0,q:"",sort:"newest",finality:"finalized"});refresh(true);});
el("prev").addEventListener("click",()=>{state.offset=Math.max(0,state.offset-50);refresh();});
el("next").addEventListener("click",()=>{state.offset+=50;refresh();});
el("refresh").addEventListener("click",()=>refresh());
el("close-dialog").addEventListener("click",()=>el("mint-dialog").close());
document.addEventListener("click",async e=>{
 const copy=e.target.closest("[data-copy]");
 if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy);notify("Адрес скопирован");}catch{notify("Копирование недоступно. Адрес можно выделить в деталях.");}return;}
 const recipient=e.target.closest("[data-recipient]");
 if(recipient){state.q=recipient.dataset.recipient;state.minimum=0;el("mint-dialog").close();refresh(true);el("search-form").scrollIntoView({behavior:"smooth",block:"center"});return;}
 const detail=e.target.closest("[data-tx]");
 if(detail)details(detail.dataset.tx,Number(detail.dataset.log));
});
if(!["#method","#mints","#minters"].includes(location.hash))history.replaceState(null,"",location.pathname+location.search+"#mints");
document.querySelectorAll("[data-chart-type]").forEach(button=>button.addEventListener("click",()=>{
 state.chartType=button.dataset.chartType;
 document.querySelectorAll("[data-chart-type]").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));
 if(state.data)renderChart(state.data);
}));
configureTableSort("mint-table",normalizeMintSort(state.sort),sort=>{state.sort=sort;refresh(true);});
window.addEventListener("hashchange",showView);
controls();showView();refresh();
window.addEventListener("resize",()=>{if(state.data)renderChart(state.data);});
setInterval(()=>{if(!document.hidden&&!state.fetching){
 const draft=el("query").value,minimumDraft=el("minimum").value;
 refresh();el("query").value=draft;el("minimum").value=minimumDraft;
}},15000);
