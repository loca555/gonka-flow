/* Exact integer data; Number is used only for pixel coordinates. No remote chart library. */
window.GonkaChart=(()=>{
 const escape=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 function exact(raw,decimals){
  const n=BigInt(raw),base=10n**BigInt(decimals),fraction=(n%base).toString().padStart(decimals,"0").replace(/0+$/,"");
  return (n/base).toLocaleString("ru-RU")+(fraction?"."+fraction:"");
 }
 function axis(raw,decimals){
  const n=BigInt(raw),base=10n**BigInt(decimals);
  if(n>=1000000n*base)return exact(n*10n/(base*1000000n),1)+" млн";
  if(n>=1000n*base)return exact(n*10n/(base*1000n),1)+" тыс";
  return exact(n,decimals);
 }
 function ceiling(n){
  if(n<=4n)return 4n;
  const step=(n+3n)/4n,power=10n**BigInt(step.toString().length-1);
  return [1n,2n,5n,10n].find(x=>x*power>=step)*power*4n;
 }
 const label=d=>d.slice(8)+"."+d.slice(5,7);
 const fullDate=d=>label(d)+"."+d.slice(0,4);
 function render(host,options){
  const {points=[],unit="WGNK",decimals=9,type="line",complete=false,title="Объём по дням",countLabel="Событий"}=options;
  if(!points.length){host.innerHTML='<p class="empty">Нет данных по выбранным фильтрам.</p>';return;}
  const lookup=new Map(points.map(p=>[p.date,p]));
  const start=Date.parse(points[0].date+"T00:00:00Z"),end=Date.parse(points[points.length-1].date+"T00:00:00Z");
  const rows=[];
  for(let t=start;t<=end;t+=86400000){
   const day=new Date(t).toISOString().slice(0,10),source=lookup.get(day);
   rows.push(source?{...source,raw:BigInt(source.raw)}:{date:day,raw:complete?0n:null,events:0});
  }
  const W=Math.max(280,host.clientWidth),H=W<500?270:330,left=W<500?62:76,right=20,top=26,bottom=H-42,plot=W-left-right;
  const high=ceiling(rows.reduce((v,r)=>r.raw!==null&&r.raw>v?r.raw:v,0n));
  const x=i=>rows.length===1?left+plot/2:left+i*plot/(rows.length-1);
  const y=raw=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  let svg='<svg viewBox="0 0 '+W+" "+H+'" role="img" tabindex="0" aria-label="'+escape(title)+'. Стрелки влево и вправо выбирают день."><defs><linearGradient id="'+host.id+'-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".28"/><stop offset="100%" stop-color="var(--accent)" stop-opacity=".015"/></linearGradient></defs>';
  svg+='<text x="'+left+'" y="13" class="chart-unit">'+escape(unit)+'</text>';
  for(let i=0;i<=4;i++){
   const raw=high*BigInt(i)/4n,py=y(raw);
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text x="'+(left-10)+'" y="'+(py+4)+'" text-anchor="end">'+escape(axis(raw,decimals))+'</text>';
  }
  const ticks=Math.min(rows.length,W<500?4:7);
  for(let i=0;i<ticks;i++){
   const index=ticks===1?0:Math.round(i*(rows.length-1)/(ticks-1)),px=x(index);
   svg+='<text x="'+px+'" y="'+(bottom+25)+'" text-anchor="'+(i===0?"start":i===ticks-1?"end":"middle")+'">'+label(rows[index].date)+'</text>';
  }
  if(type==="bars"){
   const width=Math.max(1,Math.min(24,plot/rows.length*.64));
   for(let i=0;i<rows.length;i++)if(rows[i].raw!==null){
    svg+='<rect class="chart-bar" x="'+(x(i)-width/2)+'" y="'+y(rows[i].raw)+'" width="'+width+'" height="'+(bottom-y(rows[i].raw))+'" rx="1.5"/>';
   }
  }else{
   let segment=[];
   const draw=()=>{
    if(!segment.length)return;
    const path=segment.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+y(rows[i].raw).toFixed(2)).join(" ");
    svg+='<path class="chart-area" d="'+path+" L"+x(segment[segment.length-1])+","+bottom+" L"+x(segment[0])+","+bottom+' Z" fill="url(#'+host.id+'-fill)"/><path class="chart-line" d="'+path+'"/>';
    if(segment.length===1)svg+='<circle cx="'+x(segment[0])+'" cy="'+y(rows[segment[0]].raw)+'" r="3" fill="var(--accent)"/>';
    segment=[];
   };
   rows.forEach((r,i)=>{if(r.raw===null)draw();else segment.push(i);});draw();
  }
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/><circle r="4.5"/><rect class="chart-date-box" y="'+(bottom+10)+'" height="24" width="100" rx="4"/><text class="chart-date-text" y="'+(bottom+26)+'" text-anchor="middle"></text></g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector("svg"),cursor=host.querySelector(".chart-cursor"),tooltip=host.querySelector(".chart-tooltip");
  let selected=rows.length-1;
  const show=index=>{
   selected=Math.max(0,Math.min(rows.length-1,index));
   const row=rows[selected],px=x(selected),py=row.raw===null?bottom:y(row.raw),dateX=Math.min(W-52,Math.max(52,px));
   cursor.setAttribute("visibility","visible");
   cursor.querySelector("line").setAttribute("x1",px);cursor.querySelector("line").setAttribute("x2",px);
   const dot=cursor.querySelector("circle");dot.setAttribute("cx",px);dot.setAttribute("cy",py);dot.setAttribute("visibility",row.raw===null?"hidden":"visible");
   cursor.querySelector("rect").setAttribute("x",dateX-50);
   cursor.querySelector("text").setAttribute("x",dateX);cursor.querySelector("text").textContent=fullDate(row.date);
   tooltip.innerHTML='<span>'+fullDate(row.date)+'</span><strong>'+(row.raw===null?"Нет подтверждённых данных":escape(exact(row.raw,decimals))+' <small>'+escape(unit)+'</small>')+'</strong><span>'+(row.raw===null?"Покрытие неполное":escape(countLabel)+": "+Number(row.events||0).toLocaleString("ru-RU"))+'</span>';
   tooltip.hidden=false;
   const boxWidth=tooltip.offsetWidth;
   tooltip.style.left=Math.max(6,Math.min(W-boxWidth-6,px>W/2?px-boxWidth-14:px+14))+"px";
   tooltip.style.top=Math.max(22,Math.min(H-tooltip.offsetHeight-50,py-30))+"px";
  };
  const hide=()=>{cursor.setAttribute("visibility","hidden");tooltip.hidden=true;};
  root.addEventListener("pointermove",event=>{
   const box=root.getBoundingClientRect(),px=(event.clientX-box.left)*W/box.width;
   show(rows.length===1?0:Math.round((px-left)/plot*(rows.length-1)));
  });
  root.addEventListener("pointerleave",hide);
  root.addEventListener("pointerdown",event=>{
   const box=root.getBoundingClientRect();show(Math.round(((event.clientX-box.left)*W/box.width-left)/plot*(rows.length-1)));
  });
  root.addEventListener("focus",()=>show(selected));
  root.addEventListener("blur",hide);
  root.addEventListener("keydown",event=>{
   if(["ArrowLeft","ArrowRight","Home","End","Escape"].includes(event.key))event.preventDefault();
   if(event.key==="ArrowLeft")show(selected-1);
   if(event.key==="ArrowRight")show(selected+1);
   if(event.key==="Home")show(0);
   if(event.key==="End")show(rows.length-1);
   if(event.key==="Escape")hide();
  });
 }
 return {render,exact};
})();
