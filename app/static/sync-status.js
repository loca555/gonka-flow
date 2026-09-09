"use strict";
// Each endpoint reports its own index. A complete mint index alone is not LIVE.
const GonkaSync=(()=>{
 const channels={mints:null,market:null},node=id=>document.getElementById(id);
 const number=value=>Math.trunc(value).toLocaleString("ru-RU");
 const age=packet=>packet?(Date.now()-packet.received)/1000:Infinity;
 const now=packet=>(packet?.data?.now||0)+age(packet);
 const old=(packet,ts,limit)=>!ts||now(packet)-ts>limit;
 const stamp=ts=>new Date(ts*1000).toLocaleString("ru-RU",{timeZone:"Asia/Nicosia",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
 const duration=seconds=>number(Math.floor(seconds/60))+" мин "+number(Math.floor(seconds%60))+" с";
 function render(){
  const mint=channels.mints,market=channels.market,d=mint?.data,f=market?.data;
  const c=d?.coverage,s=c?.live||{},h=c?.history||{},fc=f?.coverage,fs=f?.status||{},snapshot=f?.snapshot;
  const head=Math.max(c?.head||0,fc?.head||0);
  const latest=s.latest_height||0,waiting=Math.max(0,latest-head);
  const lag=s.latest_ts&&snapshot?.ts?Math.max(0,s.latest_ts-snapshot.ts):null;
  const mintTotal=c?.total?c.total+Math.max(0,head-c.head):0;
  const marketTotal=fc?.start&&head?head-fc.start+1:0;
  // Market indexed_height is the end of contiguous, successfully indexed ranges.
  const marketCovered=marketTotal?Math.max(0,Math.min(head,fc.indexed_height)-fc.start+1):0;
  const mintComplete=Boolean(c?.complete&&c.head>=head);
  const marketComplete=Boolean(fc?.complete&&fc.head>=head);
  const verified=Boolean(f?.ready&&snapshot?.ledger_verified&&snapshot.height>=head);
  const offline=mint?.failed||market?.failed;
  const errors=[s.error||h.error,fs.error].filter(Boolean);
  const fresh=mint&&market&&latest>=head&&age(mint)<=60&&age(market)<=60&&!old(mint,s.checked_at,90)&&!old(mint,s.latest_ts,120)&&!old(market,fs.checked_at,90);
  let phase="loading",caption="Проверяем синхронизацию · Ethereum";
  if(d?.indexer_enabled===false){phase="paused";caption="Сборщик остановлен · Ethereum";}
  else if(offline){phase="error";caption="Нет свежего ответа · Ethereum";}
  else if(errors.length){phase="error";caption="Ожидание RPC · Ethereum";}
  else if(mint&&market&&head){
   if(!mintComplete||!marketComplete){phase="syncing";caption="Синхронизация · Ethereum";}
   else if(!verified){phase="verifying";caption="Сверяем снимок · Ethereum";}
   else if(!fresh||old(market,snapshot.checked_at,360)){phase="stale";caption="Проверяем свежесть · Ethereum";}
   else if(waiting){phase="finality";caption="Ожидаем финализацию · Ethereum";}
   else{phase="live";caption="LIVE · Ethereum";}
  }
  // A stalled index must not keep claiming that it is actively loading.
  if(["syncing","verifying"].includes(phase)&&!fresh){phase="stale";caption="Проверяем свежесть · Ethereum";}
  node("live-caption").textContent=caption;
  node("status-dot").className="status-dot"+(phase==="live"?" ok":phase==="error"?" error":["syncing","verifying"].includes(phase)?" syncing":"");
  node("status-dot").closest(".connection-status").dataset.state=phase;
  node("coverage-detail").textContent=mintTotal?"Чеканка: "+number(c.covered)+" из "+number(mintTotal)+" блоков":"Чеканка: проверяем историю…";
  node("market-coverage-detail").textContent=marketTotal?"Торговля и мост: "+number(marketCovered)+" из "+number(marketTotal)+" блоков":"Торговля и мост: проверяем историю…";
  node("snapshot-detail").textContent=["live","finality"].includes(phase)?"Сверено до финального #"+number(head)+" · "+stamp(snapshot.ts):
   head?"Финальный #"+number(head)+(snapshot?" · снимок #"+number(snapshot.height):" · снимок готовится"):"Ожидаем финальный блок Ethereum";
  node("finality-detail").hidden=!head||!waiting;
  node("finality-detail").textContent=waiting?"Сеть #"+number(latest)+" · без финализации: "+number(waiting)+" блоков"+(lag===null?"":" · отставание ленты "+duration(lag)):"";
  const notes=[];
  if(mint?.failed)notes.push("Нет свежего ответа по чеканке. Повторим автоматически.");
  if(market?.failed)notes.push("Нет свежего ответа по торговле и мосту. Повторим автоматически.");
  if(s.error||h.error)notes.push("Чеканка: "+(s.error||h.error));
  if(fs.error)notes.push("Торговля и мост: "+fs.error);
  node("rpc-note").textContent=notes.join(" ");node("rpc-note").hidden=!notes.length;
 }
 const update=(name,data)=>{channels[name]={data,received:Date.now(),failed:false};render();};
 const fail=name=>{channels[name]={...channels[name],failed:true};render();};
 setInterval(render,15000);
 document.addEventListener("visibilitychange",render);
 return {update,fail};
})();
