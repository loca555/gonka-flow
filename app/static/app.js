"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nf = (n,d=2) => n===null || n===undefined ? "—" : Number(n).toLocaleString("ru-RU",{maximumFractionDigits:d});
const compact = n => Number(n)>=1e6 ? nf(Number(n)/1e6,2)+" млн" : Number(n)>=1e3 ? nf(Number(n)/1e3,1)+" тыс." : nf(n,2);
const short = a => !a ? "—" : a.length>23 ? a.slice(0,a.startsWith("gonka")?11:8)+"…"+a.slice(-6) : a;
const clock = ts => ts ? new Date(ts*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
const date = ts => ts ? new Date(ts*1000).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
const utc = ts => new Date(ts*1000).toISOString().slice(11,16);
const kinds = {buy:"Покупка",sell:"Продажа",transfer:"Перевод",bridge_lock:"GNK → мост",bridge_mint:"Выпуск WGNK",bridge_burn:"Сжигание WGNK",bridge_release:"Возврат GNK",reward_paid:"Выплата награды",reward_vested:"Награда в вестинг",vesting_unlock:"Разблокировка",vesting_credit:"Зачисление вестинга",vesting_funding:"Резерв вестинга",module_transfer:"Системный перевод",collateral_deposit:"В залог",collateral_release:"Возврат залога",fee:"Комиссия",escrow_deposit:"Перевод в escrow",liquidity_add:"Добавление LP",liquidity_remove:"Снятие LP"};
const titles = {
 overview:["Обзор сети","Капитал в движении","Кто получает GNK, куда они движутся и что происходит на рынке."],
 activity:["Поток транзакций","Следуйте за потоком","Переводы, сделки и выплаты. Каждое событие — со ссылкой на источник."],
 bridge:["Мост GNK ↔ WGNK","Между двумя сетями","От блокировки до выпуска. От сжигания до возврата GNK."],
 hosts:["Хосты и награды","Вычисления. Вознаграждения.","Активные участники сети, концентрация веса и получатели выплат."],
 holders:["Держатели GNK / WGNK","Адреса за капиталом","Балансы, роли и подтверждённая торговая активность. Адрес — не личность."],
 address:["Страница адреса","История одного адреса","Сделки, переводы и точные доказательства в доступном локальном индексе."],
 sources:["Источники и метод","Данные, которым есть основание","Покрытие индекса, состояние источников и границы интерпретации."]
};
const state = {tab:"overview",hours:24,live:true,kind:"",chain:"",minimum:0,offset:0,bridgeOffset:0,mode:"sell",reward:"rewards",labels:{},hosts:null,overview:null,busy:false,pending:false,version:0,lastRefresh:0,labelTime:0,hostTime:0,events:new Map()};
Object.assign(state,{holderAsset:"GNK",holderMinimum:10000,holderTag:"",holderQuery:"",holderOffset:0,
 address:null,addressHours:0,addressKind:"trades",addressOffset:0});
const activityLabels={buy:"Покупает",sell:"Продаёт",both:"Покупает и продаёт",none:"Сделки не выявлены"};
function profileLink(address,label){return '<a class="address" href="#address/'+encodeURIComponent(address)+'" title="'+esc(address)+'">'+esc(label||short(address))+'</a>';}
function txt(id,value){$(id).textContent=value;}
function badge(kind,label){return '<span class="badge '+esc(kind)+'">'+esc(label||kinds[kind]||kind)+'</span>';}
function addr(a,withLabel=true){
 if(!a || a==="0x"+"0".repeat(40)) return '<span class="muted">'+(a?"∅":"—")+'</span>';
 const label=state.labels[a.toLowerCase()];
 return '<button class="address" data-address="'+esc(a)+'" title="'+esc(a)+'">'+esc(short(a))+'</button>'+(withLabel&&label?'<div class="wallet-label">'+esc(label.label)+'</div>':"");
}
function txurl(e){
 if(e.chain==="ethereum") return "https://etherscan.io/tx/"+encodeURIComponent(e.tx_hash);
 return e.tx_hash.startsWith("block:") ? "https://node1.gonka.ai:8443/chain-rpc/block_results?height="+e.height :
 "https://node1.gonka.ai:8443/chain-rpc/tx?hash=0x"+encodeURIComponent(e.tx_hash);
}
function link(e,label="↗"){return e?'<a class="tx-link" href="'+esc(txurl(e))+'" target="_blank" rel="noopener noreferrer" title="Открыть источник">'+esc(label)+'</a>':"";}
function remember(e){state.events.set(e.id,e);}
function eventRows(items,cols=7){
 if(!items.length) return '<tr><td colspan="'+cols+'" class="empty">В загруженной части истории событий не найдено.<br>Проверьте период, фильтры и покрытие в разделе «Источники и метод».</td></tr>';
 return items.map(e=>{
  remember(e);
  const source=e.kind==="buy"||e.kind==="sell"?e.actor||e.src:e.src;
  let secondary=e.chain==="gonka"?"Gonka":"Ethereum";
  const raw=e.meta.attribution;
  if(raw==="initiator_only") secondary+=" · только инициатор";
  if(raw==="initiator_net") secondary+=" · движение подтверждено";
  return '<tr><td>'+clock(e.ts)+'<small>'+date(e.ts).split(",")[0]+'</small></td><td>'+badge(e.kind)+'<small>'+esc(secondary)+'</small></td><td>'+addr(source)+'</td><td>'+addr(e.dst)+'</td><td class="right">'+nf(e.amount,4)+' <span class="muted">'+e.asset+'</span>'+(Number(e.quote_amount)?'<small>'+nf(e.quote_amount,2)+' '+esc(e.quote_asset)+'</small>':"")+'</td><td class="right"><span class="'+(e.finalized?"confirmed":"unconfirmed")+'">'+(e.finalized?"✓ Финальный":"◌ Предварит.")+'</span><small>#'+nf(e.height,0)+'</small></td><td><button class="tx-link" data-event="'+esc(e.id)+'" title="Подробности события" aria-label="Подробности события">↗</button></td></tr>';
 }).join("");
}
async function api(path){
 const r=await fetch("/api/"+path,{signal:AbortSignal.timeout(30000)});
 if(!r.ok){let msg="Ошибка API "+r.status;try{const j=await r.json();if(typeof j.detail==="string")msg=j.detail;}catch{}throw new Error(msg);}
 return r.json();
}
function toast(text){txt("toast",text);$("toast").hidden=false;clearTimeout(state.toastTimer);state.toastTimer=setTimeout(()=>$("toast").hidden=true,3500);}
function total(o,kind,field="amount"){return o.totals.filter(x=>x.kind===kind).reduce((s,x)=>s+Number(x[field]),0);}
function periodParams(value=state.hours){
 if(value==="archive"){
  const since=state.overview?.history_request?.since;
  if(!since)throw new Error("Дата архива ещё не загружена");
  return {since};
 }
 return {hours:value};
}
function archiveDate(o=state.overview,full=false){
 const ts=o?.history_request?.since;
 return ts?new Date(ts*1000).toLocaleDateString("ru-RU",{timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit",...(full?{year:"numeric"}:{})}):"—";
}
function periodLabel(){return state.hours==="archive"?"с "+archiveDate(undefined,true):"за "+(state.hours===168?"7 дней":state.hours+" ч");}
function renderArchive(o){
 const request=o.history_request;
 $("archive-period").hidden=!request;
 $("profile-archive-option").hidden=!request;
 $("history-progress").hidden=!request;
 if(!request)return;
 txt("archive-period","С "+archiveDate(o));txt("profile-archive-option","С "+archiveDate(o,true)+" · МСК");
 $("archive-period").title="С "+request.iso+"; только загруженная часть истории";
 txt("history-heading","Архив с "+archiveDate(o,true));
 txt("history-cutoff",new Date(request.since*1000).toLocaleTimeString("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"})+" МСК · до финальных блоков сети");
 $("history-chains").innerHTML=["gonka","ethereum"].map(c=>{
  const a=o.archive[c],ready=a.boundary_verified,complete=a.complete;
  const percent=a.total_blocks?Math.min(complete?100:99.99,Math.floor(a.covered_blocks/a.total_blocks*10000)/100):0;
  const error=a.error||o.status[c].error;
  const retry=a.error?a:o.status[c],history=o.status[c+"_history"];
  const waiting=error&&retry.retry_at>o.now;
  const mode=c==="gonka"&&history.batch_blocks?'Пакетное чтение · '+nf(history.batch_blocks,0)+' блоков'+(history.provider?' · '+history.provider:''):'';
  return '<div class="archive-chain"><div><strong>'+(c==="gonka"?"GNK · Gonka":"WGNK · Ethereum")+'</strong><span>'+(!ready?"Проверяем границу":complete?"Загружено":nf(percent,2)+"%")+'</span></div><progress max="100" value="'+percent+'" aria-label="'+(c==="gonka"?"Архив GNK":"Архив WGNK")+'"></progress><p>'+(ready?'#'+nf(a.target_height,0)+' → #'+nf(a.head_height,0)+' · осталось '+nf(a.missing_blocks,0)+' блоков':'Проверяем блок на дату начала и предыдущий блок.')+'</p>'+(mode?'<p>'+esc(mode)+'</p>':'')+(error?'<p class="'+(waiting?'muted':'negative')+'">'+(waiting?'Пауза RPC · повтор после '+clock(retry.retry_at)+': ':'Повторяем запрос: ')+esc(error)+'</p>':"")+'</div>';
 }).join("");
}
function renderStatus(o){
 ["gonka","ethereum"].forEach((chain,i)=>{
  const s=o.status[chain],lag=chain==="gonka"?(s.remote_height||0)-(s.indexed_height||0):Math.max((s.remote_height||0)-(s.provisional_height||0),(s.finalized_height||0)-(s.indexed_height||0));
  const stale=!s.checked_at||o.now-s.checked_at>90;
  const error=!!s.error&&!s.ok,catching=lag>(i?8:12);
  const label=error?"ошибка RPC":!s.checked_at?"подключение…":stale?"снимок устарел":catching?"догоняем · "+nf(lag,0)+" блоков":"синхронизирован";
  $(i?"eth-status":"gonka-status").innerHTML='<span class="status-dot '+(error?"error":stale||catching?"loading":"")+'"></span> '+(i?"Ethereum":"Gonka")+' <strong>#'+nf(s.indexed_height,0)+'</strong><span>'+label+'</span>';
 });
 const partial=["gonka","ethereum"].some(c=>!o.period_complete?.[c]);
 $("coverage-note").classList.toggle("complete",!partial);
 $("coverage-note").innerHTML=(partial?'<span class="tiny-spinner"></span> ':'✓ ')+(partial?
  'Покрытие периода неполное. Загруженные события — не весь оборот '+periodLabel()+'. ':
  'Финальные блоки выбранного периода загружены. ')+
  'Предварительные события Ethereum могут измениться.';
 txt("last-update","Обновлено "+clock(o.now));
 txt("footer-count",nf(o.stored_events,0)+" событий в локальном индексе");
 txt("volume-period",state.hours==="archive"?"С "+archiveDate():state.hours===168?"7 Д":state.hours+" Ч");
 $("export-link").href="/api/export.csv?"+new URLSearchParams(periodParams());
 renderArchive(o);
}
function renderOverview(o){
 const market=o.snapshots.market;
 const pairs=(market?.data||[]).filter(p=>o.pools[p.pairAddress.toLowerCase()]);
 const pair=pairs.sort((a,b)=>(b.liquidity?.usd||0)-(a.liquidity?.usd||0))[0];
 if(pair){
  txt("price","$"+nf(pair.priceUsd,5));
  const change=Number(pair.priceChange?.h24||0);
  txt("price-change",(change>=0?"+":"")+nf(change,2)+"% · 24 ч"+(o.now-market.updated_at>300?" · устарело":""));
  $("price-change").className=change>=0?"positive":"negative";
  $("price").title="Цена снимка DexScreener · "+date(market.updated_at)+". Ликвидность пула: $"+nf(pair.liquidity?.usd,0);
 }
 const buys=total(o,"buy"),sells=total(o,"sell"),quote=total(o,"buy","quote")+total(o,"sell","quote");
 txt("dex-volume",o.coverage.ethereum.blocks?nf(quote,0):"—");
 txt("swap-count",nf(o.totals.filter(x=>["buy","sell"].includes(x.kind)).reduce((n,x)=>n+x.n,0),0)+" Swap-событий · индекс");
 const supply=o.snapshots.wgnk_supply;
 txt("wrapped-supply",supply?compact(Number(supply.data.amount)/1e9):"—");
 txt("escrow-caption",o.snapshots.escrow?"Escrow: "+compact(Number(o.snapshots.escrow.data.amount)/1e9)+" GNK":"Escrow: ожидаем RPC");
 txt("bridge-out",o.coverage.gonka.blocks?nf(total(o,"bridge_lock"),1):"—");
 txt("bridge-in",o.coverage.ethereum.blocks?nf(total(o,"bridge_burn"),1):"—");
 const net=buys-sells;
 txt("net-flow","Покупки − продажи: "+(net>=0?"+":"")+compact(net)+" WGNK");
 renderChart(o);
}
function renderChart(o){
 const rows=o.buckets.filter(x=>["buy","sell"].includes(x.kind));
 if(!rows.length){$("volume-chart").innerHTML='<div class="empty">В проиндексированном диапазоне Swap-событий пока нет.<br>График появится по мере загрузки истории.</div>';return;}
 const end=Math.floor(o.now/3600)*3600,start=Math.floor(o.since/3600)*3600;
 const span=(end-start)/3600,step=span>168?Math.max(24,Math.ceil(span/120))*3600:span>48?6*3600:3600,bars=[];
 for(let ts=start;ts<=end;ts+=step)bars.push({ts,buy:0,sell:0});
 rows.forEach(r=>{let n=Math.floor((r.ts-start)/step);if(bars[n])bars[n][r.kind]+=Number(r.amount);});
 const max=Math.max(...bars.map(b=>Math.max(b.buy,b.sell)),1), W=760,H=205,left=54,right=15,top=13,zero=92,bottom=175,width=W-left-right,unit=width/bars.length;
 let svg='<svg viewBox="0 0 '+W+" "+H+'" role="img" aria-label="Объём покупок и продаж WGNK по временным интервалам">';
 [top,zero,bottom].forEach((y,i)=>{svg+='<line x1="'+left+'" y1="'+y+'" x2="'+(W-right)+'" y2="'+y+'" stroke="'+(i===1?"var(--chart-axis)":"var(--chart-grid)")+'" stroke-dasharray="'+(i===1?"0":"3 4")+'"/><text x="'+(left-9)+'" y="'+(y+3)+'" text-anchor="end">'+(i===1?"0":compact(max))+'</text>';});
 bars.forEach((b,i)=>{
  const x=left+i*unit+unit*.18, w=Math.max(1,unit*.62), bh=b.buy/max*(zero-top),sh=b.sell/max*(bottom-zero);
  svg+='<g><title>'+date(b.ts)+' · интервал '+nf(step/3600,0)+' ч, UTC-группировка · покупки '+nf(b.buy,2)+' / продажи '+nf(b.sell,2)+' WGNK</title>';
  if(bh)svg+='<rect x="'+x+'" y="'+(zero-bh)+'" width="'+w+'" height="'+bh+'" rx="1.4" fill="var(--buy)"/>';
  if(sh)svg+='<rect x="'+x+'" y="'+zero+'" width="'+w+'" height="'+sh+'" rx="1.4" fill="var(--sell)"/>';
  if(i%Math.max(1,Math.floor(bars.length/6))===0)svg+='<text x="'+(x+w/2)+'" y="194" text-anchor="middle">'+(span>48?new Date(b.ts*1000).toISOString().slice(5,10):utc(b.ts))+'</text>';
  svg+='</g>';
 });
 $("volume-chart").innerHTML=svg+"</svg>";
}
function renderRanks(data,reward=false){
 const id=reward?"rewards-body":"rank-body";
 if(!data.items.length){$(id).innerHTML='<tr><td colspan="'+(reward?5:6)+'" class="empty">Подтверждённых '+(reward?"выплат":"участников")+" в загруженном диапазоне пока нет.</td></tr>";return;}
 $(id).innerHTML=data.items.slice(0,reward?40:15).map((r,i)=>'<tr><td class="muted">'+String(i+1).padStart(2,"0")+'</td><td>'+addr(r.address)+'</td>'+(reward?'<td>'+badge(r.kind)+'</td>':"")+'<td>'+r.n+'</td><td class="right">'+nf(r.amount,3)+'</td>'+(reward?"":'<td class="right">'+nf(r.quote,2)+'</td><td>'+date(r.last)+'</td>')+'</tr>').join("");
}
function paginate(prefix,data,limit=50){
 $(prefix+"-prev").disabled=data.offset===0;$(prefix+"-next").disabled=!data.has_more;
 txt(prefix+"-page",data.total?(data.offset+1)+"–"+Math.min(data.offset+limit,data.total)+" / "+nf(data.total,0):"0 событий");
}
const bridgeLabels={completed:"Сопоставлено",awaiting_counterpart:"Нет второй стороны в индексе",origin_not_indexed:"Источник не загружен",provisional:"Предварительно"};
function bridgeCard(b){
 const isOut=b.direction==="out",left=isOut?"GNK":"WGNK",right=isOut?"WGNK":"GNK";
 if(b.native)remember(b.native);if(b.ethereum)remember(b.ethereum);
 return '<article class="bridge-card"><div><div class="route-title">'+left+'<span>→</span>'+right+'</div>'+badge(b.status,bridgeLabels[b.status])+'</div><div class="route-addresses"><div>'+addr(b.source)+'<small>'+(isOut?"Gonka":"Ethereum")+'</small></div><span>→</span><div>'+addr(b.target)+'<small>'+(isOut?"Ethereum":"Gonka")+'</small></div></div><div class="bridge-amount">'+nf(b.amount,3)+'<small>GNK / WGNK · 1:1</small></div><footer><span>'+date(b.ts)+(b.seconds!==null?' · между этапами '+nf(b.seconds/60,1)+' мин':"")+'</span><span>'+link(b.native,"Gonka ↗")+link(b.ethereum,"Ethereum ↗")+(b.receipt?.status==="completed"?' · запись Gonka подтверждена':"")+'</span></footer></article>';
}
function renderHosts(h){
 state.hosts=h;
 txt("host-count",h.epoch===null?"—":nf(h.items.length,0));
 txt("epoch-label","ЭПОХА "+(h.epoch||"—"));
 txt("concentration","Доля веса топ-10: "+nf(h.top10_share,1)+"%");
 txt("total-weight",nf(h.total_weight,0));
 txt("host-snapshot-time","Снимок "+date(h.updated_at));
 txt("weight-top","Топ-10 хостов: "+nf(h.top10_share,2)+"%");
 const colors=Array.from({length:10},(_,i)=>"var(--weight-"+(i+1)+")");
 let html=h.items.slice(0,10).map((p,i)=>'<div title="'+esc(p.address)+' · '+nf(p.share,2)+'%" style="width:'+p.share+'%;background:'+colors[i]+'"></div>').join("");
 if(h.items.length)html+='<div title="Остальные хосты" style="flex:1;background:var(--weight-rest)"></div>';
 $("weight-chart").innerHTML=html;filterHosts();
}
function filterHosts(){
 if(!state.hosts)return;
 const q=$("host-filter").value.trim().toLowerCase();
 const rows=state.hosts.items.map((p,i)=>({...p,rank:i+1})).filter(p=>[p.address,p.endpoint,...p.models].join(" ").toLowerCase().includes(q));
 $("hosts-body").innerHTML=rows.length?rows.map(p=>{
  let endpoint=p.endpoint;try{endpoint=new URL(p.endpoint).host;}catch{}
  return '<tr><td class="muted">'+String(p.rank).padStart(2,"0")+'</td><td>'+addr(p.address,false)+'</td><td title="'+esc(p.endpoint)+'">'+esc(endpoint||"не указан")+'</td><td class="host-models">'+p.models.map(esc).join("<br>")+'</td><td class="right">'+nf(p.weight,0)+'</td><td class="right">'+nf(p.share,2)+'%<span class="share-bar"><i style="width:'+Math.min(100,p.share*4)+'%"></i></span></td></tr>';
 }).join(""):'<tr><td colspan="6" class="empty">Хостов по этому фильтру не найдено.</td></tr>';
}
function renderSources(o){
 let cards=["gonka","ethereum"].map(c=>{
  const s=o.status[c],h=o.status[c+"_history"],cov=o.coverage[c];
  return '<article class="source-card"><h3>'+(c==="gonka"?"Gonka · CometBFT":"Ethereum · EVM")+badge(s.ok?"completed":"provisional",s.ok?"Доступен":"Ожидание / ошибка")+'</h3><p>'+esc(s.provider||"Подключаем RPC")+(s.error?"<br>"+esc(s.error):"")+(h.error?"<br>История: "+esc(h.error):"")+'</p><dl><dt>Последний блок сети</dt><dd>'+nf(s.remote_height,0)+'</dd><dt>Индекс · финальный блок</dt><dd>'+nf(s.indexed_height,0)+'</dd><dt>Обработано блоков</dt><dd>'+nf(cov.blocks,0)+'</dd><dt>Начало покрытия</dt><dd>'+date(cov.first_time)+'</dd><dt>Очередь истории</dt><dd>'+nf(h.remaining,0)+'</dd><dt>Последний успешный запрос</dt><dd>'+date(s.checked_at)+'</dd></dl><p class="ranges">Диапазоны: '+(cov.ranges.map(r=>r.lo+"–"+r.hi).join(" · ")||"ещё нет")+'</p></article>';
 }).join("");
 cards+='<article class="source-card"><h3>Рынок и пулы</h3><p>Цена и ликвидность: DexScreener (внешний снимок). Сделки: логи проверенных onchain-пулов.</p><dl><dt>Проверено пулов V3</dt><dd>'+Object.keys(o.pools).length+'</dd><dt>Неохваченные найденные пулы</dt><dd>'+o.undecoded_pools.length+'</dd><dt>Снимок рынка</dt><dd>'+date(o.snapshots.market?.updated_at)+'</dd></dl><p class="ranges">'+Object.keys(o.pools).map(esc).join("<br>")+'</p></article>';
 cards+='<article class="source-card"><h3>Точность и хранение</h3><p>Суммы хранятся как целые nGNK. GNK и WGNK: 9 знаков; USDT: 6. Отображение округлено, точные значения доступны в деталях и CSV.</p><dl><dt>Событий в SQLite</dt><dd>'+nf(o.stored_events,0)+'</dd><dt>Хосты · снимок</dt><dd>'+date(state.hosts?.updated_at)+'</dd><dt>Общая эмиссия GNK</dt><dd>'+(o.snapshots.supply?nf(Number(o.snapshots.supply.data.amount)/1e9,2):"—")+'</dd></dl></article>';
 $("sources-grid").innerHTML=cards;
}
function renderHolders(h){
 const c=h.census||{},s=h.collector||{},isNative=h.asset==="GNK";
 txt("holder-asset-label",h.asset);
 txt("holder-count",nf(h.total,0)+" адресов ≥ "+nf(h.minimum,0)+" "+h.asset+" в загруженной выборке");
 let title=isNative?(c.complete?"Снимок GNK сверён с эмиссией":"Перепись GNK ещё загружается"):(c.complete?"Реестр WGNK восстановлен":"Восстанавливаем WGNK от выпуска контракта");
 let detail=isNative?"Обработано "+nf(c.seen||0,0)+" из "+(c.total?nf(c.total,0):"—")+" адресов. Балансы снимка — блок #"+nf(c.height,0)+". Изменившиеся адреса дополнительно перепроверяются по RPC.":
  "Балансы восстановлены на блок #"+nf(c.height,0)+(c.block_time?" · "+date(c.block_time):"")+". "+(c.remaining_blocks?"До финального блока сети осталось "+nf(c.remaining_blocks,0)+" блоков. Это промежуточные исторические балансы.":"Отдельная история сделок догружается независимо.");
 const progress=isNative?(c.total?(c.seen||0)/c.total:0):(c.head&&c.deployment?((c.height||c.deployment)-c.deployment+1)/(c.head-c.deployment+1):0);
 $("holder-census").innerHTML='<strong>'+title+'</strong><span class="holder-meta">'+esc(detail)+'</span>'+
  (!c.complete?'<div class="holder-progress"><i style="width:'+Math.max(0,Math.min(100,progress*100))+'%"></i></div>':"")+
  (s.error?'<span class="holder-meta negative">Источник: '+esc(s.error)+'. Сохраняем последний успешный результат.</span>':"");
 $("holders-body").innerHTML=h.items.length?h.items.map((r,i)=>
  '<tr><td class="muted">'+nf(h.offset+i+1,0)+'</td><td>'+profileLink(r.address)+(r.label?'<span class="holder-role">'+esc(r.label.label)+'</span>':"")+'</td><td class="right" title="'+esc(r.balance)+' '+h.asset+'">'+nf(r.balance,4)+' <span class="muted">'+h.asset+'</span><small>#'+nf(r.height,0)+' · '+date(r.updated_at)+'</small></td><td>'+badge(r.tag,activityLabels[r.tag])+'</td><td class="right">'+(r.buys?nf(r.bought,3):"—")+'</td><td class="right">'+(r.sells?nf(r.sold,3):"—")+'</td><td>'+date(r.last_trade)+'</td><td>'+profileLink(r.address,"↗")+'</td></tr>'
 ).join(""):'<tr><td colspan="8" class="empty">В загруженной выборке нет адресов по этим фильтрам.<br>Проверьте порог баланса и прогресс переписи.</td></tr>';
 paginate("holders",h);
}
function renderAddress(p){
 const a=p.activity,holder=p.holder,eth=p.asset==="WGNK",cov=p.coverage;
 txt("profile-address",p.address);txt("profile-network",eth?"ETHEREUM / WGNK":"GONKA / GNK");txt("profile-asset",p.asset);
 $("profile-role").innerHTML=(p.label?badge("",p.label.label)+" ":"")+badge(a.tag,activityLabels[a.tag]);
 txt("profile-balance",holder?compact(holder.balance):"—");
 $("profile-balance").title=holder?holder.balance+" "+p.asset:"Баланс ещё не измерен";
 txt("profile-balance-time",holder?"Блок #"+nf(holder.height,0)+" · "+date(holder.updated_at):"Адрес ещё не попал в перепись");
 txt("profile-bought",eth?compact(a.bought):"—");txt("profile-sold",eth?compact(a.sold):"—");txt("profile-net",eth?compact(a.net):"—");
 txt("profile-buys",eth?nf(a.buys,0)+" покупок · "+nf(a.buy_quote,2)+" USDT":"GNK и WGNK не объединяются");
 txt("profile-sells",eth?nf(a.sells,0)+" продаж · "+nf(a.sell_quote,2)+" USDT":"DEX-сделки — на Ethereum");
 const queue=state.overview?.status[eth?"ethereum_history":"gonka_history"]?.remaining||0;
 $("profile-coverage").innerHTML='<strong>История операций: '+nf(p.events.total,0)+' событий по выбранному фильтру.</strong><span class="holder-meta">Покрытие блоков: '+date(cov.first_time)+' — '+date(cov.last_time)+'. '+nf(cov.ranges.length,0)+' диапазон(а). '+(queue?"В очереди ещё "+nf(queue,0)+" блоков. ":"")+'«Весь локальный индекс» включает все доступные страницы, но не гарантирует полноту истории сети.</span>';
 txt("profile-note",p.note+(eth?"":" Нативные GNK-переводы не классифицируются как покупки или продажи. Для них выберите «Все операции»."));
 $("profile-body").innerHTML=eventRows(p.events.items);paginate("profile",p.events);
 document.querySelectorAll("[data-profile-kind]").forEach(el=>el.classList.toggle("selected",el.dataset.profileKind===p.kind));
}
async function refresh(force=false){
 if(!state.live&&!force)return;
 if(state.busy){state.pending=true;return;}
 state.busy=true;state.pending=false;
 const version=state.version,tab=state.tab,hours=state.hours;
 try{
  const period=periodParams(hours),query=new URLSearchParams(period).toString();
  const tasks=[api("overview?"+query)];
  const labelsDue=Date.now()-state.labelTime>60000,hostsDue=Date.now()-state.hostTime>60000;
  tasks.push(labelsDue?api("labels"):Promise.resolve(state.labels));
  tasks.push(hostsDue?api("hosts"):Promise.resolve(state.hosts));
  if(tab==="overview")tasks.push(api("events?"+query+"&limit=8&minimum=1"),api("rankings?"+query+"&mode="+state.mode));
  if(tab==="activity")tasks.push(api("events?"+new URLSearchParams({...period,limit:50,offset:state.offset,kind:state.kind,chain:state.chain,minimum:state.minimum})));
  if(tab==="bridge")tasks.push(api("bridges?"+query+"&offset="+state.bridgeOffset));
  if(tab==="hosts")tasks.push(api("rankings?"+query+"&mode="+state.reward));
  if(tab==="holders")tasks.push(api("holders?"+new URLSearchParams({asset:state.holderAsset,...period,minimum:state.holderMinimum,tag:state.holderTag,q:state.holderQuery,limit:50,offset:state.holderOffset})));
  if(tab==="address")tasks.push(api("addresses/"+encodeURIComponent(state.address)+"?"+new URLSearchParams({...periodParams(state.addressHours),kind:state.addressKind,limit:50,offset:state.addressOffset})));
  const [o,labels,h,...data]=await Promise.all(tasks);
  if(version!==state.version){state.pending=true;return;}
  state.overview=o;state.labels=labels;
  if(labelsDue)state.labelTime=Date.now();if(hostsDue)state.hostTime=Date.now();
  renderStatus(o);renderOverview(o);if(h)renderHosts(h);
  if(tab==="overview"){$("recent-body").innerHTML=eventRows(data[0].items);renderRanks(data[1]);}
  if(tab==="activity"){$("activity-body").innerHTML=eventRows(data[0].items);txt("event-count",nf(data[0].total,0)+" событий");paginate("events",data[0]);}
  if(tab==="bridge"){$("bridge-list").innerHTML=data[0].items.map(bridgeCard).join("")||'<div class="panel empty">В загруженной истории переводов через мост пока нет.</div>';txt("bridge-count",nf(data[0].total,0)+" маршрутов");paginate("bridges",data[0]);}
  if(tab==="hosts")renderRanks(data[0],true);
  if(tab==="sources")renderSources(o);
  if(tab==="holders")renderHolders(data[0]);
  if(tab==="address")renderAddress(data[0]);
  $("error-banner").hidden=true;state.lastRefresh=Date.now();
  if(state.events.size>2000){const keep=[...state.events.entries()].slice(-1000);state.events=new Map(keep);}
 }catch(e){txt("error-banner","Не удалось обновить данные: "+e.message+". Последний успешный снимок сохранён.");$("error-banner").hidden=false;}
 finally{state.busy=false;if(state.pending){state.pending=false;refresh(true);}}
}
function changed(){state.version++;refresh(true);}
function tab(name){
 if(!titles[name])return;
 state.tab=name;const [crumb,title,description]=titles[name];
 document.querySelectorAll(".view").forEach(x=>x.hidden=x.id!=="view-"+name);
 document.querySelectorAll(".nav-item").forEach(x=>x.classList.toggle("active",x.dataset.tab===name));
 document.querySelector(".period-control").hidden=name==="address";
 $("coverage-note").hidden=name==="address";
 txt("breadcrumb",crumb);$("page-title").innerHTML=esc(title)+'<span class="accent">.</span>';txt("page-description",description);
 history.replaceState(null,"","#"+name+(name==="address"?"/"+encodeURIComponent(state.address):""));changed();window.scrollTo({top:0,behavior:"instant"});
}
function route(){
 const hash=location.hash.slice(1);
 if(hash.startsWith("address/")){
  let address;try{address=decodeURIComponent(hash.slice(8)).toLowerCase();}catch{return tab("holders");}
  if(!/^(0x[0-9a-f]{40}|gonka1[023456789acdefghjklmnpqrstuvwxyz]{38,80})$/.test(address)){tab("holders");toast("Некорректный адрес");return;}
  if(state.address!==address){state.address=address;state.addressOffset=0;state.addressHours=0;$("profile-period").value="0";state.addressKind=address.startsWith("gonka")?"all":"trades";}
  state.walletRequest=null;if($("detail-dialog").open)$("detail-dialog").close();
  tab("address");
 }else tab(titles[hash]?hash:"overview");
}
function openDrawer(label,html){txt("drawer-label",label);$("drawer-content").innerHTML=html;if(!$("detail-dialog").open)$("detail-dialog").showModal();}
async function wallet(address){
 const key=address.toLowerCase();state.walletRequest=key;
 openDrawer("КОШЕЛЁК",'<div class="drawer-address">'+esc(key)+'</div><div class="empty"><span class="tiny-spinner"></span> Получаем баланс и историю…</div>');
 try{
  const w=await api("wallet/"+encodeURIComponent(key)+"?"+new URLSearchParams(periodParams()));
  if(state.walletRequest!==key)return;
  let html='<div class="drawer-address">'+esc(key)+'</div><button class="button-secondary" data-copy="'+esc(key)+'">Копировать адрес</button>'+(w.label?'<div class="drawer-note">'+esc(w.label.label)+" · "+esc(w.label.source)+'</div>':"")+'<div class="drawer-balance">'+(w.balance?nf(w.balance.amount,5):"—")+' <span class="muted">'+(w.balance?.asset||"")+'</span></div><p class="drawer-note">'+(w.error?esc(w.error):"Баланс на "+date(w.updated_at)+". Не включает отдельные модули вестинга и залога.")+'</p><h3 class="drawer-subhead">Связанные переводы через мост</h3>';
  html='<p class="drawer-note">'+profileLink(key,"Открыть страницу адреса и всю доступную историю ↗")+'</p>'+html;
  html+=w.bridges.length?w.bridges.map(b=>'<div class="drawer-route">'+badge(b.status,bridgeLabels[b.status])+'<br>'+addr(b.source,false)+' → '+addr(b.target,false)+'<br>'+nf(b.amount,4)+' GNK / WGNK · '+date(b.ts)+'</div>').join(""):'<p class="drawer-note">Сопоставленных маршрутов в выбранном периоде не найдено.</p>';
  html+='<p class="drawer-note">Связь адресов показывает маршрут перевода, не общего владельца и не происхождение каждой монеты.</p><h3 class="drawer-subhead">Активность '+periodLabel()+'</h3>';
  html+=w.events.items.map(e=>{remember(e);return '<div class="drawer-event"><div>'+badge(e.kind)+'<span class="muted">'+date(e.ts)+'</span></div><div><strong>'+nf(e.amount,4)+' '+e.asset+'</strong><br><button class="text-button" data-event="'+esc(e.id)+'">Детали ↗</button></div></div>';}).join("")||'<p class="drawer-note">В локальном индексе событий нет.</p>';
  if(w.events.has_more)html+='<p class="drawer-note">Показаны последние 100 из '+nf(w.events.total,0)+' событий.</p>';
  openDrawer("КОШЕЛЁК · ТОЛЬКО ЧТЕНИЕ",html);
 }catch(e){if(state.walletRequest===key)openDrawer("КОШЕЛЁК",'<div class="error-banner">'+esc(e.message)+'</div>');}
}
function eventDetail(id){
 const e=state.events.get(id);if(!e)return;
 state.walletRequest=null;
 openDrawer("ДОКАЗАТЕЛЬСТВО · ОНЧЕЙН-СОБЫТИЕ",badge(e.kind)+'<div class="drawer-balance">'+nf(e.amount,6)+' <span class="muted">'+e.asset+'</span></div><p class="drawer-note">'+date(e.ts)+' · '+(e.finalized?"финальный блок":"предварительный блок, возможна реорганизация")+'</p><div class="drawer-key">Отправитель</div>'+addr(e.src)+'<div class="drawer-key">Получатель</div>'+addr(e.dst)+(e.actor?'<div class="drawer-key">Инициатор / actor</div>'+addr(e.actor):"")+'<div class="drawer-key">Транзакция / блок</div><div class="drawer-address">'+esc(e.tx_hash)+'</div>'+link(e,"Проверить в источнике ↗")+'<div class="drawer-key">Точные суммы и основание классификации</div><pre>'+esc(JSON.stringify(e,null,2))+'</pre>');
}
document.addEventListener("click",e=>{
 const el=e.target.closest("button");if(!el)return;
 if(el.dataset.tab)tab(el.dataset.tab);
 if(el.dataset.hours){state.hours=el.dataset.hours==="archive"?"archive":Number(el.dataset.hours);state.offset=state.bridgeOffset=state.holderOffset=0;document.querySelectorAll("[data-hours]").forEach(b=>b.classList.toggle("selected",b===el));changed();}
 if("kind" in el.dataset){state.kind=el.dataset.kind;state.offset=0;document.querySelectorAll("[data-kind]").forEach(b=>b.classList.toggle("selected",b===el));changed();}
 if(el.dataset.mode){state.mode=el.dataset.mode;document.querySelectorAll("[data-mode]").forEach(b=>b.classList.toggle("selected",b===el));changed();}
 if(el.dataset.reward){state.reward=el.dataset.reward;document.querySelectorAll("[data-reward]").forEach(b=>b.classList.toggle("selected",b===el));changed();}
 if(el.dataset.asset){state.holderAsset=el.dataset.asset;state.holderOffset=0;document.querySelectorAll("[data-asset]").forEach(b=>b.classList.toggle("selected",b===el));changed();}
 if(el.dataset.profileKind){state.addressKind=el.dataset.profileKind;state.addressOffset=0;changed();}
 if(el.dataset.address)wallet(el.dataset.address);
 if(el.dataset.event)eventDetail(el.dataset.event);
 if(el.dataset.copy)navigator.clipboard.writeText(el.dataset.copy).then(()=>toast("Адрес скопирован")).catch(()=>toast("Не удалось скопировать. Выделите адрес вручную."));
});
$("wallet-search").addEventListener("submit",e=>{e.preventDefault();const a=$("search-address").value.trim();if(a)wallet(a);});
$("close-dialog").onclick=()=>{$("detail-dialog").close();state.walletRequest=null;};
$("detail-dialog").addEventListener("close",()=>state.walletRequest=null);
$("live-toggle").onclick=()=>{state.live=!state.live;$("live-toggle").classList.toggle("paused",!state.live);$("live-toggle").setAttribute("aria-pressed",String(state.live));$("live-toggle").lastElementChild.textContent=state.live?"LIVE":"ПАУЗА";toast(state.live?"Автообновление включено":"Пауза экрана. Индексатор продолжает собирать данные.");if(state.live)refresh(true);};
$("chain-filter").onchange=()=>{state.chain=$("chain-filter").value;state.offset=0;changed();};
$("minimum-filter").onchange=()=>{state.minimum=Math.max(0,Math.min(1e12,Math.floor(Number($("minimum-filter").value)||0)));$("minimum-filter").value=state.minimum;state.offset=0;changed();};
$("host-filter").oninput=filterHosts;
$("holder-minimum").onchange=()=>{state.holderMinimum=Math.max(0,Math.min(1e12,Math.floor(Number($("holder-minimum").value)||0)));$("holder-minimum").value=state.holderMinimum;state.holderOffset=0;changed();};
$("holder-tag").onchange=()=>{state.holderTag=$("holder-tag").value;state.holderOffset=0;changed();};
$("holder-search").onsubmit=e=>{e.preventDefault();state.holderQuery=$("holder-query").value.trim();state.holderOffset=0;changed();};
$("profile-period").onchange=()=>{state.addressHours=$("profile-period").value==="archive"?"archive":Number($("profile-period").value);state.addressOffset=0;changed();};
$("profile-copy").onclick=()=>navigator.clipboard.writeText(state.address).then(()=>toast("Адрес скопирован")).catch(()=>toast("Не удалось скопировать. Выделите адрес вручную."));
$("profile-current").onclick=()=>wallet(state.address);
["holders","profile"].forEach(prefix=>["prev","next"].forEach(direction=>$(prefix+"-"+direction).onclick=()=>{const field=prefix==="holders"?"holderOffset":"addressOffset";state[field]=Math.max(0,state[field]+(direction==="next"?50:-50));changed();}));
["events","bridges"].forEach(prefix=>["prev","next"].forEach(direction=>$(prefix+"-"+direction).onclick=()=>{const field=prefix==="events"?"offset":"bridgeOffset";state[field]=Math.max(0,state[field]+(direction==="next"?50:-50));changed();}));
const stream=new EventSource("/api/stream");
stream.onmessage=()=>{if(Date.now()-state.lastRefresh>8000)refresh();};
stream.onerror=()=>{txt("last-update","Переподключение live-канала…");};
setInterval(()=>{if(!document.hidden&&Date.now()-state.lastRefresh>14000)refresh();},5000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
window.addEventListener("hashchange",route);
route();
