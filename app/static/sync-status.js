"use strict";
// Each endpoint reports its own index. A complete mint index alone is not LIVE.
const GonkaSync=(()=>{
 const channels={mints:null,market:null},node=id=>document.getElementById(id);
 const age=packet=>packet?(Date.now()-packet.received)/1000:Infinity;
 const now=packet=>(packet?.data?.now||0)+age(packet);
 const old=(packet,ts,limit)=>!ts||now(packet)-ts>limit;
 const blocks=value=>{
  const last=value%10,pair=value%100;
  return value.toLocaleString("ru-RU")+" "+(pair>=11&&pair<=14?"блоков":last===1?"блок":last>=2&&last<=4?"блока":"блоков");
 };
 function progressText(mint,market,head,phase,complete,verified){
  const parts=[],snapshot=market?.snapshot,coverage=market?.coverage;
  if(snapshot?.ts){
   const stamp=new Date(snapshot.ts*1000),options={timeZone:market.timezone||"Asia/Nicosia"};
   parts.push(stamp.toLocaleDateString("ru-RU",{...options,day:"2-digit",month:"2-digit",year:"numeric"})+" "+
    stamp.toLocaleTimeString("ru-RU",{...options,hour:"2-digit",minute:"2-digit",second:"2-digit"}));
  }
  if(!mint?.coverage||!coverage)parts.push("получаем состояние истории");
  else{
   const mintMissing=mint.coverage.missing;
   const marketMissing=Number.isFinite(coverage.indexed_height)?Math.max(0,head-coverage.indexed_height,coverage.missing||0):coverage.missing;
   const action=phase==="syncing"?"догружаем ":"осталось загрузить ";
   if(marketMissing>0&&mintMissing>0)parts.push("история неполная", "торговля и мост: "+action+blocks(marketMissing),"чеканка: "+action+blocks(mintMissing));
   else if(marketMissing>0||mintMissing>0)parts.push("история неполная, "+action+blocks(marketMissing>0?marketMissing:mintMissing));
   else if(complete)parts.push(verified?"история без пропусков":"история загружена, сверяем снимок");
   else parts.push("проверяем полноту истории");
  }
  return parts.join(" · ");
 }
 function render(){
  const mint=channels.mints,market=channels.market,d=mint?.data,f=market?.data;
  const c=d?.coverage,s=c?.live||{},h=c?.history||{},fc=f?.coverage,fs=f?.status||{},snapshot=f?.snapshot;
  const head=Math.max(fc?.head||0,s.latest_height||0,fs.latest_height||0);
  const mintComplete=Boolean(c?.complete);
  const marketComplete=Boolean(fc?.complete&&fc.head>=head);
  const verified=Boolean(f?.ready&&snapshot?.ledger_verified&&snapshot.height>=head);
  const offline=mint?.failed||market?.failed;
  const errors=[s.error||h.error,fs.error].filter(Boolean);
  const fresh=mint&&market&&age(mint)<=60&&age(market)<=60&&!old(mint,s.checked_at,90)&&!old(mint,s.latest_ts,120)&&!old(market,fs.checked_at,90);
  let phase="loading",caption="Проверяем синхронизацию · Ethereum";
  if(d?.indexer_enabled===false){phase="paused";caption="Сборщик остановлен · Ethereum";}
  else if(offline){phase="error";caption="Нет свежего ответа · Ethereum";}
  else if(errors.length){phase="error";caption="Ожидание RPC · Ethereum";}
  else if(mint&&market&&head){
   if(!mintComplete||!marketComplete){phase="syncing";caption="Синхронизация · Ethereum";}
   else if(!verified){phase="verifying";caption="Сверяем снимок · Ethereum";}
   else if(!fresh||old(market,snapshot.checked_at,360)){phase="stale";caption="Проверяем свежесть · Ethereum";}
   else{phase="live";caption="LIVE · Ethereum";}
  }
  // A stalled index must not keep claiming that it is actively loading.
  if(["syncing","verifying"].includes(phase)&&!fresh){phase="stale";caption="Проверяем свежесть · Ethereum";}
  node("live-caption").textContent=caption;
  node("status-dot").className="status-dot"+(phase==="live"?" ok":phase==="error"?" error":["syncing","verifying"].includes(phase)?" syncing":"");
  node("status-dot").closest(".connection-status").dataset.state=phase;
  node("sync-progress").textContent=progressText(d,f,head,phase,mintComplete&&marketComplete,verified);
  node("sync-progress").hidden=false;
  node("sync-progress").title="Время последнего проверенного снимка · Кипр";
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
