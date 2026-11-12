"use strict";
// Each endpoint reports its own index. A complete mint index alone is not LIVE.
const GonkaSync=(()=>{
 const channels={mints:null,market:null},node=id=>document.getElementById(id);
 const age=packet=>packet?(Date.now()-packet.received)/1000:Infinity;
 const now=packet=>(packet?.data?.now||0)+age(packet);
 const old=(packet,ts,limit)=>!ts||now(packet)-ts>limit;
 const blocks=value=>{
  return value.toLocaleString("en-US")+" "+(value===1?"block":"blocks");
 };
 function progressText(mint,market,head,phase,complete,verified){
  const parts=[],snapshot=market?.snapshot,coverage=market?.coverage;
  if(snapshot?.ts){
   const stamp=new Date(snapshot.ts*1000),options={timeZone:market.timezone||"Asia/Nicosia"};
   parts.push(stamp.toLocaleDateString("en-US",{...options,day:"2-digit",month:"2-digit",year:"numeric"})+" "+
    stamp.toLocaleTimeString("en-US",{...options,hour:"2-digit",minute:"2-digit",second:"2-digit"}));
  }
  if(!mint?.coverage||!coverage)parts.push("fetching history status");
  else{
   const mintMissing=mint.coverage.missing;
   const marketMissing=Number.isFinite(coverage.indexed_height)?Math.max(0,head-coverage.indexed_height,coverage.missing||0):coverage.missing;
   const action=phase==="syncing"?"loading ":"left to load ";
   if(marketMissing>0&&mintMissing>0)parts.push("history incomplete", "trading and bridge: "+action+blocks(marketMissing),"mints: "+action+blocks(mintMissing));
   else if(marketMissing>0||mintMissing>0)parts.push("history incomplete, "+action+blocks(marketMissing>0?marketMissing:mintMissing));
   else if(complete)parts.push(verified?"history without gaps":"history loaded, reconciling snapshot");
   else parts.push("checking history completeness");
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
  let phase="loading",caption="Checking sync · Ethereum";
  if(d?.indexer_enabled===false){phase="paused";caption="Collector stopped · Ethereum";}
  else if(offline){phase="error";caption="No fresh response · Ethereum";}
  else if(errors.length){phase="error";caption="Waiting for RPC · Ethereum";}
  else if(mint&&market&&head){
   if(!mintComplete||!marketComplete){phase="syncing";caption="Syncing · Ethereum";}
   else if(!verified){phase="verifying";caption="Reconciling snapshot · Ethereum";}
   else if(!fresh||old(market,snapshot.checked_at,360)){phase="stale";caption="Checking freshness · Ethereum";}
   else{phase="live";caption="LIVE · Ethereum";}
  }
  // A stalled index must not keep claiming that it is actively loading.
  if(["syncing","verifying"].includes(phase)&&!fresh){phase="stale";caption="Checking freshness · Ethereum";}
  node("live-caption").textContent=caption;
  node("status-dot").className="status-dot"+(phase==="live"?" ok":phase==="error"?" error":["syncing","verifying"].includes(phase)?" syncing":"");
  node("status-dot").closest(".connection-status").dataset.state=phase;
  node("sync-progress").textContent=progressText(d,f,head,phase,mintComplete&&marketComplete,verified);
  node("sync-progress").hidden=false;
  node("sync-progress").title="Time of the last verified snapshot · Cyprus";
  const notes=[];
  if(mint?.failed)notes.push("No fresh response for mints. Will retry automatically.");
  if(market?.failed)notes.push("No fresh response for trading and bridge. Will retry automatically.");
  if(s.error||h.error)notes.push("Mints: "+(s.error||h.error));
  if(fs.error)notes.push("Trading and bridge: "+fs.error);
  node("rpc-note").textContent=notes.join(" ");node("rpc-note").hidden=!notes.length;
 }
 const update=(name,data)=>{channels[name]={data,received:Date.now(),failed:false};render();};
 const fail=name=>{channels[name]={...channels[name],failed:true};render();};
 setInterval(render,15000);
 document.addEventListener("visibilitychange",render);
 return {update,fail};
})();
