/* Exact integer data; Number is used only for pixel coordinates. No remote chart library. */
window.GonkaChart=(()=>{
 function formatPrice(raw){
  if(raw===null||raw===undefined)return "—";
  const n=BigInt(raw),factor=1000000n,rounded=(n+factor/2n)/factor;
  if(n>0n&&rounded===0n)return "< 0.000001";
  const fraction=(rounded%factor).toString().padStart(6,"0").replace(/0+$/,"");
  return (rounded/factor).toLocaleString("ru-RU")+(fraction?"."+fraction:"");
 }
 const escape=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 function exact(raw,decimals){
  const n=BigInt(raw),base=10n**BigInt(decimals);
  return n>0n&&n<base?"< 1":(n/base).toLocaleString("ru-RU");
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
 function renderMarket(host,points,{side="all"}={}){
  if(!points.length){host.innerHTML='<p class="empty">Нет сделок по выбранным фильтрам.</p>';return;}
  const lookup=new Map(points.map(p=>[p.date,p])),rows=[];
  const end=Date.parse(points[points.length-1].date+"T00:00:00Z");
  for(let ts=Date.parse(points[0].date+"T00:00:00Z");ts<=end;ts+=86400000){
   const day=new Date(ts).toISOString().slice(0,10),p=lookup.get(day);
   rows.push({date:day,volume:p?BigInt(p.raw):0n,price:p?BigInt(p.price_raw):null,events:p?.events||0,
    buy:p?BigInt(p.bought_raw):0n,sell:p?BigInt(p.sold_raw):0n,
    buyQuote:p?BigInt(p.buy_quote_raw):0n,sellQuote:p?BigInt(p.sale_quote_raw):0n,
    buys:p?.buys_count||0,sales:p?.sales_count||0});
  }
  const W=Math.max(280,host.clientWidth),H=W<500?320:350;
  const left=W<500?48:76,right=W<500?54:80,top=W<500?52:30,bottom=H-42,plot=W-left-right;
  const volumeHigh=ceiling(rows.reduce((a,r)=>r.volume>a?r.volume:a,0n));
  const priceHigh=ceiling(rows.reduce((a,r)=>r.price!==null&&r.price>a?r.price:a,0n));
  const x=i=>rows.length===1?left+plot/2:left+i*plot/(rows.length-1);
  const y=(raw,high)=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  const kinds=side==="all"?["buy","sell"]:[side],names={buy:"Покупки",sell:"Продажи"};
  let svg='<svg class="market-chart" viewBox="0 0 '+W+" "+H+'" role="img" tabindex="0" aria-label="Цена линией. '+(side==="all"?"Покупки и продажи WGNK раздельными частями дневных столбцов":names[side]+" WGNK столбцами по дням")+'. Цена в USDT за 1 WGNK. Стрелки выбирают день.">';
  svg+='<text class="chart-unit price-axis" x="'+left+'" y="14">USDT / 1 WGNK</text><text class="chart-unit volume-axis" x="'+(W-right)+'" y="'+(W<500?33:14)+'" text-anchor="end">Объём · WGNK</text>';
  for(let i=0;i<=4;i++){
   const py=bottom-i*(bottom-top)/4;
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text class="price-axis" x="'+(left-9)+'" y="'+(py+4)+'" text-anchor="end">'+escape(formatPrice(priceHigh*BigInt(i)/4n))+'</text><text class="volume-axis" x="'+(W-right+9)+'" y="'+(py+4)+'">'+escape(axis(volumeHigh*BigInt(i)/4n,9))+'</text>';
  }
  const barWidth=Math.max(1,Math.min(24,plot/rows.length*.65));
  rows.forEach((r,i)=>{
   let base=0n;
   kinds.forEach(kind=>{
    const volume=r[kind],total=base+volume;
    if(volume>0n)svg+='<rect class="market-volume '+kind+'" data-kind="'+kind+'" data-date="'+r.date+'" data-raw="'+volume+'" x="'+(x(i)-barWidth/2)+'" y="'+y(total,volumeHigh)+'" width="'+barWidth+'" height="'+(y(base,volumeHigh)-y(total,volumeHigh))+'" rx="1"><title>'+fullDate(r.date)+' · '+names[kind]+': '+escape(exact(volume,9))+' WGNK</title></rect>';
    base=total;
   });
  });
  let segment=[];
  const draw=()=>{
   if(!segment.length)return;
   svg+='<path class="market-price" d="'+segment.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+y(rows[i].price,priceHigh).toFixed(2)).join(" ")+'"/>';
   if(segment.length===1)svg+='<circle cx="'+x(segment[0])+'" cy="'+y(rows[segment[0]].price,priceHigh)+'" r="3" fill="var(--market-price)"/>';
   segment=[];
  };
  rows.forEach((r,i)=>{if(r.price===null)draw();else segment.push(i);});draw();
  const ticks=Math.min(rows.length,W<500?4:7);
  for(let i=0;i<ticks;i++){
   const index=ticks===1?0:Math.round(i*(rows.length-1)/(ticks-1));
   svg+='<text x="'+x(index)+'" y="'+(bottom+25)+'" text-anchor="'+(i===0?"start":i===ticks-1?"end":"middle")+'">'+label(rows[index].date)+'</text>';
  }
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/><circle r="4"/></g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector("svg"),cursor=host.querySelector(".chart-cursor"),tooltip=host.querySelector(".chart-tooltip");
  let selected=rows.length-1;
  function show(index){
   selected=Math.max(0,Math.min(rows.length-1,index));
   const r=rows[selected],px=x(selected);
   cursor.setAttribute("visibility","visible");
   const line=cursor.querySelector("line");line.setAttribute("x1",px);line.setAttribute("x2",px);
   const dot=cursor.querySelector("circle");dot.setAttribute("cx",px);dot.setAttribute("cy",r.price===null?bottom:y(r.price,priceHigh));dot.setAttribute("visibility",r.price===null?"hidden":"visible");
   tooltip.innerHTML='<span>'+fullDate(r.date)+'</span><strong>Цена: '+(r.price===null?"нет сделок":escape(formatPrice(r.price)))+'</strong><span>USDT за 1 WGNK · средневзвешенная</span>'+
    '<div class="market-tooltip-sides">'+kinds.map(kind=>'<div data-trade-kind="'+kind+'"><span class="trade-key '+kind+'">'+names[kind]+'</span><b>'+escape(exact(r[kind],9))+' WGNK</b><small>'+escape(exact(r[kind+"Quote"],6))+' USDT · '+(kind==="buy"?r.buys:r.sales).toLocaleString("ru-RU")+' исп.</small></div>').join("")+'</div>'+
    '<strong>Объём: '+escape(exact(r.volume,9))+' <small>WGNK</small></strong><span>Исполнений: '+r.events.toLocaleString("ru-RU")+'</span>';
   tooltip.hidden=false;
   tooltip.style.left=Math.max(6,Math.min(W-tooltip.offsetWidth-6,px>W/2?px-tooltip.offsetWidth-14:px+14))+"px";
   tooltip.style.top=Math.max(4,Math.min(top+8,H-tooltip.offsetHeight-8))+"px";
  }
  const hide=()=>{cursor.setAttribute("visibility","hidden");tooltip.hidden=true;};
  const pointer=event=>{
   const box=root.getBoundingClientRect(),px=(event.clientX-box.left)*W/box.width;
   show(rows.length===1?0:Math.round((px-left)/plot*(rows.length-1)));
  };
  root.addEventListener("pointermove",pointer);root.addEventListener("pointerdown",pointer);
  root.addEventListener("pointerleave",hide);root.addEventListener("focus",()=>show(selected));root.addEventListener("blur",hide);
  root.addEventListener("keydown",event=>{
   if(["ArrowLeft","ArrowRight","Home","End","Escape"].includes(event.key))event.preventDefault();
   if(event.key==="ArrowLeft")show(selected-1);if(event.key==="ArrowRight")show(selected+1);
   if(event.key==="Home")show(0);if(event.key==="End")show(rows.length-1);if(event.key==="Escape")hide();
  });
 }
 function renderAddress(host,history){
  if(!history){host.innerHTML='<p class="empty">Подтверждённая история баланса пока недоступна.</p>';return;}
  if(!history.points.length){host.innerHTML='<p class="empty">У адреса пока нет движений WGNK до проверенного снимка.</p>';return;}
  const rows=history.points.map(p=>({...p,buy:BigInt(p.bought_raw),sell:BigInt(p.sold_raw),balance:BigInt(p.balance_raw)}));
  const W=Math.max(280,host.clientWidth),small=W<500,H=small?300:310;
  const left=small?54:76,right=small?58:80,top=48,bottom=H-38,plot=W-left-right;
  const volumeHigh=ceiling(rows.reduce((max,r)=>r.buy>max?(r.buy>r.sell?r.buy:r.sell):r.sell>max?r.sell:max,4000000000n));
  const balanceHigh=ceiling(rows.reduce((max,r)=>r.balance>max?r.balance:max,4000000000n));
  const slot=plot/rows.length,x=i=>left+(i+.5)*slot;
  const y=(raw,high)=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  const names={buy:"Покупки",sell:"Продажи"};
  let svg='<svg class="address-chart" viewBox="0 0 '+W+' '+H+'" role="img" tabindex="0" aria-label="Покупки и продажи WGNK по дням — зелёные и красные столбцы, шкала слева. Баланс WGNK — синяя линия, шкала справа. Стрелки выбирают день.">';
  svg+='<text class="chart-unit" x="'+left+'" y="15">Объём / день</text><text class="chart-unit" x="'+left+'" y="33">WGNK</text><text class="chart-unit balance-axis" x="'+(W-right)+'" y="15" text-anchor="end">Баланс</text><text class="chart-unit balance-axis" x="'+(W-right)+'" y="33" text-anchor="end">WGNK</text>';
  for(let i=0;i<=4;i++){
   const py=bottom-i*(bottom-top)/4;
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text x="'+(left-8)+'" y="'+(py+4)+'" text-anchor="end">'+escape(axis(volumeHigh*BigInt(i)/4n,9))+'</text><text class="balance-axis" x="'+(W-right+8)+'" y="'+(py+4)+'">'+escape(axis(balanceHigh*BigInt(i)/4n,9))+'</text>';
  }
  const width=Math.min(14,slot*.36),gap=Math.min(2,slot*.08);
  rows.forEach((row,i)=>{
   for(const kind of ["buy","sell"]){
    if(row[kind]<=0n)continue;
    const px=x(i)+(kind==="buy"?-gap/2-width:gap/2),py=y(row[kind],volumeHigh);
    svg+='<rect class="address-volume '+kind+'" data-kind="'+kind+'" data-date="'+row.date+'" data-raw="'+row[kind]+'" x="'+px+'" y="'+py+'" width="'+width+'" height="'+(bottom-py)+'" rx=".6"><title>'+fullDate(row.date)+' · '+names[kind]+': '+escape(exact(row[kind],9))+' WGNK</title></rect>';
   }
  });
  svg+='<path class="address-balance-line" d="'+rows.map((r,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(r.balance,balanceHigh).toFixed(2)).join(' ')+'"/>';
  const last=rows.length-1;
  svg+='<circle class="address-balance-last" cx="'+x(last)+'" cy="'+y(rows[last].balance,balanceHigh)+'" r="3.5" data-balance-raw="'+rows[last].balance+'"/>';
  const ticks=Math.min(rows.length,small?3:7);
  for(let i=0;i<ticks;i++){
   const index=ticks===1?0:Math.round(i*(rows.length-1)/(ticks-1));
   svg+='<text x="'+x(index)+'" y="'+(bottom+25)+'" text-anchor="'+(ticks===1?'middle':i===0?'start':i===ticks-1?'end':'middle')+'">'+label(rows[index].date)+'</text>';
  }
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/><circle r="4.5"/></g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip address-chart-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector('svg'),cursor=host.querySelector('.chart-cursor'),tooltip=host.querySelector('.chart-tooltip');
  let selected=last;
  function show(index){
   selected=Math.max(0,Math.min(last,index));
   const row=rows[selected],px=x(selected),onSnapshot=selected===last;
   cursor.setAttribute('visibility','visible');
   const line=cursor.querySelector('line');line.setAttribute('x1',px);line.setAttribute('x2',px);
   const dot=cursor.querySelector('circle');dot.setAttribute('cx',px);dot.setAttribute('cy',y(row.balance,balanceHigh));
   const time=new Date(history.ts*1000).toLocaleTimeString('ru-RU',{timeZone:history.timezone,hour:'2-digit',minute:'2-digit'});
   tooltip.innerHTML='<span>'+fullDate(row.date)+(onSnapshot?' · до '+escape(time):'')+'</span><strong class="balance-value">'+escape(exact(row.balance,9))+' <small>WGNK</small></strong><span>Баланс '+(onSnapshot?'на снимке':'на конец дня')+'</span><div class="address-tooltip-sides">'+['buy','sell'].map(kind=>'<div data-trade-kind="'+kind+'"><span class="trade-key '+kind+'">'+names[kind]+'</span><b>'+escape(exact(row[kind],9))+' WGNK</b><small>'+(kind==='buy'?row.buys_count:row.sales_count).toLocaleString('ru-RU')+' исп.</small></div>').join('')+'</div>';
   tooltip.dataset.date=row.date;tooltip.dataset.balanceRaw=row.balance.toString();tooltip.hidden=false;
   tooltip.style.left=Math.max(6,Math.min(W-tooltip.offsetWidth-6,px>W/2?px-tooltip.offsetWidth-12:px+12))+'px';
   tooltip.style.top=Math.max(4,Math.min(top+8,H-tooltip.offsetHeight-8))+'px';
  }
  const hide=()=>{cursor.setAttribute('visibility','hidden');tooltip.hidden=true;};
  const pointer=event=>{
   const box=root.getBoundingClientRect(),px=(event.clientX-box.left)*W/box.width;
   show(Math.floor((px-left)/slot));
  };
  root.addEventListener('pointermove',pointer);root.addEventListener('pointerdown',pointer);
  root.addEventListener('pointerleave',hide);root.addEventListener('focus',()=>show(selected));root.addEventListener('blur',hide);
  root.addEventListener('keydown',event=>{
   if(['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))event.preventDefault();
   if(event.key==='ArrowLeft')show(selected-1);if(event.key==='ArrowRight')show(selected+1);
   if(event.key==='Home')show(0);if(event.key==='End')show(last);if(event.key==='Escape')hide();
  });
 }
 return {render,renderMarket,renderAddress,exact,formatPrice};
})();
