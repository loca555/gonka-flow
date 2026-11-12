/* Exact integer data; Number is used only for pixel coordinates. No remote chart library. */
window.GonkaChart=(()=>{
 function formatPrice(raw){
  if(raw===null||raw===undefined)return "—";
  const n=BigInt(raw),factor=1000000n,rounded=(n+factor/2n)/factor;
  if(n>0n&&rounded===0n)return "< 0.000001";
  const fraction=(rounded%factor).toString().padStart(6,"0").replace(/0+$/,"");
  return (rounded/factor).toLocaleString("en-US")+(fraction?"."+fraction:"");
 }
 const escape=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 function exact(raw,decimals){
  const n=BigInt(raw),base=10n**BigInt(decimals);
  return n>0n&&n<base?"< 1":(n/base).toLocaleString("en-US");
 }
 function axis(raw,decimals){
  const n=BigInt(raw),base=10n**BigInt(decimals);
  if(n<0n)return '−'+axis(-n,decimals);
  for(const [scale,suffix] of [[1000000000000n,'T'],[1000000000n,'B'],[1000000n,'M'],[1000n,'K']]){
   if(n>=scale*base){const tenths=n*10n/(scale*base);return (tenths/10n).toString()+(tenths%10n?'.'+(tenths%10n):'')+' '+suffix;}
  }
  return exact(n,decimals);
 }
 function ceiling(n){
  if(n<=4n)return 4n;
  const step=(n+3n)/4n,power=10n**BigInt(step.toString().length-1);
  return [1n,2n,5n,10n].find(x=>x*power>=step)*power*4n;
 }
 const label=d=>d.slice(8)+"/"+d.slice(5,7);
 const fullDate=d=>label(d)+"/"+d.slice(0,4);
 function render(host,options){
  const {points=[],unit="WGNK",decimals=9,type="line",complete=false,title="Volume by day",countLabel="Events",legend=null}=options;
  if(!points.length){host.innerHTML='<p class="empty">No data for the selected filters.</p>';return;}
  const lookup=new Map(points.map(p=>[p.date,p]));
  const start=Date.parse(points[0].date+"T00:00:00Z"),end=Date.parse(points[points.length-1].date+"T00:00:00Z");
  const rows=[];
  for(let t=start;t<=end;t+=86400000){
   const day=new Date(t).toISOString().slice(0,10),source=lookup.get(day);
   rows.push(source?{...source,raw:BigInt(source.raw)}:{date:day,raw:complete?0n:null,events:0});
  }
  // Independent scales for the reference series.
  const prices=rows.map(r=>r.price_raw==null?null:BigInt(r.price_raw));
  const weights=rows.map(r=>r.weight==null?null:BigInt(r.weight));
  const tokens=rows.map(r=>r.tokens==null?null:BigInt(r.tokens));
  const bounds=series=>{
   let lo=null,hi=null;
   series.forEach(v=>{if(v!==null){if(lo===null||v<lo)lo=v;if(hi===null||v>hi)hi=v;}});
   if(lo!==null){if(lo===hi){lo-=10n;hi+=10n;}else{const span=hi-lo;lo-=span/10n;hi+=span/10n;}lo=lo<0n?0n:lo;}
   return [lo,hi];
  };
  const [pMin,pMax]=bounds(prices),[wMin,wMax]=bounds(weights),[tMin,tMax]=bounds(tokens);
  const hasPrice=pMin!==null,hasWeight=wMin!==null,hasTokens=tMin!==null,hasLines=hasPrice||hasWeight||hasTokens;
  const W=Math.max(hasWeight||hasTokens?620:280,host.clientWidth),H=W<500?330:390,top=26,bottom=H-42;
  const baseLeft=W<500?62:76;
  const axWeight=hasWeight?78:0;
  const axTokens=hasTokens?78:0;
  const left=baseLeft+axWeight+axTokens;
  const right=hasPrice?78:20;
  const plot=W-left-right;
  const mapFull=(v,lo,hi)=>bottom-Number((v-lo)*1000000n/(hi-lo))/1000000*(bottom-top);
  const yPrice=v=>mapFull(v,pMin,pMax);
  const yWeight=v=>mapFull(v,wMin,wMax);
  const yTokens=v=>mapFull(v,tMin,tMax);
  // Trailing 7-day moving average over the known daily values.
  const ma=rows.map((r,i)=>{
   let sum=0n,count=0;
   for(let j=Math.max(0,i-6);j<=i;j++){
    const v=rows[j].raw;
    if(v!==null){sum+=v;count++;}
   }
   return count?sum/BigInt(count):null;
  });
  const high=ceiling(rows.reduce((v,r)=>r.raw!==null&&r.raw>v?r.raw:v,0n));
  const x=i=>rows.length===1?left+plot/2:left+i*plot/(rows.length-1);
  const y=raw=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  const legendHtml=Array.isArray(legend)?'<div class="chart-legend">'+legend.map(item=>
   '<span class="chart-legend-item">'+(item.bar?'<i class="chart-swatch-bar" style="background:'+item.color+'"></i>':'<i class="chart-swatch-line" style="background:'+item.color+'"></i>')+escape(item.label)+'</span>').join('')+'</div>':'';
  let svg=legendHtml+'<svg viewBox="0 0 '+W+" "+H+'" role="img" tabindex="0" aria-label="'+escape(title)+'. Left and right arrows select a day."><defs><linearGradient id="'+host.id+'-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".28"/><stop offset="100%" stop-color="var(--accent)" stop-opacity=".015"/></linearGradient></defs>';
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
  if(hasWeight){
   const axX=baseLeft-8;
   svg+='<text class="chart-weight-axis" x="'+axX+'" y="13" text-anchor="end">Network weight</text>';
   for(let k=0;k<=2;k++){
    const wv=wMin+(wMax-wMin)*BigInt(k)/2n,wy=yWeight(wv);
    svg+='<text class="chart-weight-axis" x="'+axX+'" y="'+(wy+4)+'" text-anchor="end">'+escape(axis(wv,0))+'</text>';
   }
   let wSeg=[];
   const drawWeight=()=>{
    if(wSeg.length){
     const path=wSeg.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+yWeight(weights[i]).toFixed(2)).join(" ");
     svg+='<path class="chart-weight-line" d="'+path+'"/>';
    }
    wSeg=[];
   };
   weights.forEach((v,i)=>{if(v===null)drawWeight();else wSeg.push(i);});drawWeight();
  }
  if(hasTokens){
   const axX=baseLeft+axWeight-8;
   svg+='<text class="chart-tokens-axis" x="'+axX+'" y="13" text-anchor="end">AI tokens</text>';
   for(let k=0;k<=2;k++){
    const tv=tMin+(tMax-tMin)*BigInt(k)/2n,ty=yTokens(tv);
    svg+='<text class="chart-tokens-axis" x="'+axX+'" y="'+(ty+4)+'" text-anchor="end">'+escape(axis(tv,0))+'</text>';
   }
   let tSeg=[];
   const drawTokens=()=>{
    if(tSeg.length){
     const path=tSeg.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+yTokens(tokens[i]).toFixed(2)).join(" ");
     svg+='<path class="chart-tokens-line" d="'+path+'"/>';
     if(tSeg.length===1)svg+='<circle class="chart-tokens-point" cx="'+x(tSeg[0])+'" cy="'+yTokens(tokens[tSeg[0]])+'" r="3.5" fill="#d63384"/>';
    }
    tSeg=[];
   };
   tokens.forEach((v,i)=>{if(v===null)drawTokens();else tSeg.push(i);});drawTokens();
  }
  if(hasPrice){
   for(let k=0;k<=2;k++){
    const pv=pMin+(pMax-pMin)*BigInt(k)/2n,py=yPrice(pv);
    svg+='<text class="chart-price-axis" x="'+(W-right+8)+'" y="'+(py+4)+'">'+escape(formatPrice(pv))+'</text>';
   }
   let pSeg=[];
   const drawPrice=()=>{
    if(pSeg.length){
     const path=pSeg.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+yPrice(prices[i]).toFixed(2)).join(" ");
     svg+='<path class="chart-price-line" d="'+path+'"/>';
    }
    pSeg=[];
   };
   prices.forEach((v,i)=>{if(v===null)drawPrice();else pSeg.push(i);});drawPrice();
  }
  let maSeg=[];
  const drawMa=()=>{
   if(maSeg.length){
    const path=maSeg.map((i,j)=>(j?"L":"M")+x(i).toFixed(2)+","+y(ma[i]).toFixed(2)).join(" ");
    svg+='<path class="chart-ma" d="'+path+'"/>';
   }
   maSeg=[];
  };
  rows.forEach((r,i)=>{if(ma[i]===null)drawMa();else maSeg.push(i);});drawMa();
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/><circle r="4.5"/><rect class="chart-date-box" y="'+(bottom+10)+'" height="24" width="100" rx="4"/><text class="chart-date-text" y="'+(bottom+26)+'" text-anchor="middle"></text></g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip mint-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector("svg"),cursor=host.querySelector(".chart-cursor"),tooltip=host.querySelector(".chart-tooltip");
  root.style.minWidth=W+'px';
  if(hasTokens)cursor.insertAdjacentHTML('beforeend','<circle class="chart-token-cursor" r="4.5" style="fill:#d63384"/>');
  let selected=rows.length-1;
  const show=index=>{
   selected=Math.max(0,Math.min(rows.length-1,index));
   const row=rows[selected],px=x(selected),py=row.raw===null?bottom:y(row.raw),dateX=Math.min(W-52,Math.max(52,px));
   cursor.setAttribute("visibility","visible");
   cursor.querySelector("line").setAttribute("x1",px);cursor.querySelector("line").setAttribute("x2",px);
   const dot=cursor.querySelector("circle");dot.setAttribute("cx",px);dot.setAttribute("cy",py);dot.setAttribute("visibility",row.raw===null?"hidden":"visible");
   const tokenDot=cursor.querySelector('.chart-token-cursor');
   if(tokenDot){tokenDot.setAttribute('cx',px);tokenDot.setAttribute('cy',tokens[selected]===null?bottom:yTokens(tokens[selected]));tokenDot.setAttribute('visibility',tokens[selected]===null?'hidden':'visible');}
   cursor.querySelector("rect").setAttribute("x",dateX-50);
   cursor.querySelector("text").setAttribute("x",dateX);cursor.querySelector("text").textContent=fullDate(row.date);
   tooltip.innerHTML='<span>'+fullDate(row.date)+'</span><strong>'+(row.raw===null?"No confirmed data":escape(exact(row.raw,decimals))+' <small>'+escape(unit)+'</small>')+'</strong><span>'+(row.raw===null?"Coverage incomplete":escape(countLabel)+": "+Number(row.events||0).toLocaleString("en-US"))+'</span>'+
    (row.raw!==null&&ma[selected]!==null?'<span>7-day average: '+escape(exact(ma[selected],decimals))+' '+escape(unit)+'</span>':'')+
    (row.raw!==null&&prices[selected]!==null?'<span class="chart-price-axis">price: '+escape(formatPrice(prices[selected]))+' USDT</span>':'')+
    (row.weight!=null?'<span class="chart-weight-axis">network weight: '+Number(row.weight).toLocaleString("en-US")+'</span>':'')+
    (hasTokens?'<span class="chart-tokens-axis">AI tokens per epoch: '+(row.tokens!=null?Number(row.tokens).toLocaleString("en-US"):'no data')+'</span>':'');
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
  if(!points.length){host.innerHTML='<p class="empty">No trades for the selected filters.</p>';return;}
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
  const kinds=side==="all"?["buy","sell"]:[side],names={buy:"Buys",sell:"Sells"};
  let svg='<svg class="market-chart" viewBox="0 0 '+W+" "+H+'" role="img" tabindex="0" aria-label="Price as a line. '+(side==="all"?"WGNK buys and sells as separate parts of daily bars":names[side]+" of WGNK as daily bars")+'. Price in USDT per 1 WGNK. Arrows select a day.">';
  svg+='<text class="chart-unit price-axis" x="'+left+'" y="14">USDT / 1 WGNK</text><text class="chart-unit volume-axis" x="'+(W-right)+'" y="'+(W<500?33:14)+'" text-anchor="end">Volume · WGNK</text>';
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
   tooltip.innerHTML='<span>'+fullDate(r.date)+'</span><strong>Price: '+(r.price===null?"no trades":escape(formatPrice(r.price)))+'</strong><span>USDT per 1 WGNK · volume-weighted</span>'+
    '<div class="market-tooltip-sides">'+kinds.map(kind=>'<div data-trade-kind="'+kind+'"><span class="trade-key '+kind+'">'+names[kind]+'</span><b>'+escape(exact(r[kind],9))+' WGNK</b><small>'+escape(exact(r[kind+"Quote"],6))+' USDT · '+(kind==="buy"?r.buys:r.sales).toLocaleString("en-US")+' exec.</small></div>').join("")+'</div>'+
    '<strong>Volume: '+escape(exact(r.volume,9))+' <small>WGNK</small></strong><span>Executions: '+r.events.toLocaleString("en-US")+'</span>';
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
 function renderLiquidity(host,bands,{levels=["2"],prices=null,fee=3000}={}){
  if(!bands||!Object.keys(bands).length){host.innerHTML='<p class="empty">Pool liquidity is still being collected: the history of pool Mint/Burn events is required.</p>';return;}
  const chosen=levels.filter(l=>bands[l]);
  if(!chosen.length){host.innerHTML='<p class="empty">Select at least one range level.</p>';return;}
  // Merge levels by date, carrying the last known depth across quiet days.
  const byDate=new Map();
  chosen.forEach(level=>bands[level].forEach(p=>{
   if(!byDate.has(p.date))byDate.set(p.date,{});
   byDate.get(p.date)[level]=p;
  }));
  const days=[...byDate.keys()].sort();
  const rows=[];const carried={};
  for(const day of days){
   const point=byDate.get(day);
   chosen.forEach(l=>{if(point[l])carried[l]=point[l];});
   rows.push({date:day,...Object.fromEntries(chosen.map(l=>[l,carried[l]||null]))});
  }
  // Price overlay: day VWAP carried across days without trades.
  const priceSeries=[];let lastPrice=null;
  rows.forEach(r=>{
   if(prices&&prices[r.date])lastPrice=Number(BigInt(prices[r.date])/10n**6n)/1e6;
   priceSeries.push(lastPrice);
  });
  const hasPrice=priceSeries.some(v=>v!==null);
  let pMin=Infinity,pMax=-Infinity;
  priceSeries.forEach(v=>{if(v!==null){if(v<pMin)pMin=v;if(v>pMax)pMax=v;}});
  if(hasPrice){if(pMin===pMax){pMin*=.95;pMax*=1.05;}const padP=(pMax-pMin)*.08;pMin-=padP;pMax+=padP;}
  // Lines and tooltip share the same executable USDT amounts and scale.
  let high=0n;
  rows.forEach(r=>chosen.forEach(l=>{if(r[l]){
   for(const key of ['up_usdt_raw','down_usdt_raw']){const value=BigInt(r[l][key]);if(value>high)high=value;}
  }}));
  if(high<=0n){host.innerHTML='<p class="empty">No liquidity data.</p>';return;}
  high=ceiling(high);
  const W=Math.max(280,host.clientWidth),H=W<500?300:330;
  const left=W<500?68:84,right=hasPrice?78:20,top=42,bottom=H-42,plot=W-left-right;
  const x=i=>rows.length===1?left+plot/2:left+i*plot/(rows.length-1);
  const yVolume=v=>bottom-Number(v*1000000n/high)/1000000*(bottom-top);
  const yPrice=v=>bottom-(v-pMin)/(pMax-pMin)*(bottom-top);
  let svg='<svg class="liquidity-chart" viewBox="0 0 '+W+" "+H+'" role="img" tabindex="0" aria-label="Buy and sell volume in USDT, shared scale on the left. WGNK price, scale on the right. Arrows select a day.">';
  svg+='<text class="chart-unit" x="'+left+'" y="15">Volume · USDT</text>';
  if(hasPrice)svg+='<text class="chart-unit" x="'+(W-right)+'" y="30" text-anchor="end">Price · USDT / WGNK</text>';
  for(let i=0;i<=4;i++){
   const py=bottom-i*(bottom-top)/4;
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text x="'+(left-8)+'" y="'+(py+4)+'" text-anchor="end">'+escape(axis(high*BigInt(i)/4n,6))+'</text>'+
    (hasPrice?'<text class="chart-price-axis" x="'+(W-right+8)+'" y="'+(py+4)+'">$'+(pMin+(pMax-pMin)*i/4).toFixed(4)+'</text>':'');
  }
  chosen.forEach(level=>{
   const up=[],down=[];
   rows.forEach((r,i)=>{
    if(!r[level])return;
    up.push((up.length?"L":"M")+x(i).toFixed(2)+","+yVolume(BigInt(r[level].up_usdt_raw)).toFixed(2));
    down.push((down.length?"L":"M")+x(i).toFixed(2)+","+yVolume(BigInt(r[level].down_usdt_raw)).toFixed(2));
   });
   if(up.length)svg+='<path class="liq-line liq-l'+level+'" data-level="'+level+'" data-side="up" d="'+up.join(" ")+'"/>';
   if(down.length)svg+='<path class="liq-line liq-down liq-l'+level+'" data-level="'+level+'" data-side="down" d="'+down.join(" ")+'"/>';
   const latest=rows[rows.length-1][level];
   if(latest){
    const value=BigInt(latest.down_usdt_raw),py=yVolume(value);
    svg+='<line class="liq-current liq-line liq-l'+level+'" data-level="'+level+'" data-raw="'+value+'" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'" style="stroke-dasharray:6 5;stroke-width:1;opacity:.8;pointer-events:none"/>';
    svg+='<text class="liq-current-label" x="'+(left+6)+'" y="'+(py-7)+'" style="paint-order:stroke;stroke:var(--panel);stroke-width:4px;stroke-linejoin:round">Sell −'+level+'%: '+escape(exact(value,6))+' USDT</text>';
   }
  });
  if(hasPrice){
   let pd="";priceSeries.forEach((v,i)=>{if(v===null)return;pd+=(pd?"L":"M")+x(i).toFixed(2)+","+yPrice(v).toFixed(2);});
   if(pd)svg+='<path class="chart-price-line" d="'+pd+'"/>';
  }
  const ticks=Math.min(rows.length,W<500?4:7);
  for(let i=0;i<ticks;i++){
   const index=ticks===1?0:Math.round(i*(rows.length-1)/(ticks-1));
   svg+='<text x="'+x(index)+'" y="'+(bottom+25)+'" text-anchor="'+(i===0?"start":i===ticks-1?"end":"middle")+'">'+label(rows[index].date)+'</text>';
  }
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/></g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip liquidity-chart-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector("svg"),cursor=host.querySelector(".chart-cursor"),tooltip=host.querySelector(".chart-tooltip");
  let selected=rows.length-1;
  function show(index){
   selected=Math.max(0,Math.min(rows.length-1,index));
   const r=rows[selected],px=x(selected);
   cursor.setAttribute("visibility","visible");
   const line=cursor.querySelector("line");line.setAttribute("x1",px);line.setAttribute("x2",px);
   tooltip.innerHTML='<span>'+fullDate(r.date)+'</span>'+
    (hasPrice&&priceSeries[selected]!==null?'<div class="market-tooltip-band liq-price-band"><span>Day price</span><b>$'+priceSeries[selected].toFixed(4)+' <em>USDT</em></b></div>':'')+
    chosen.map(l=>r[l]&&r[l].up_usdt_raw?'<div class="market-tooltip-band liq-b'+l+'" title="Price after trade: ↑ $'+(+r[l].up_final_price).toFixed(4)+' · ↓ $'+(+r[l].down_final_price).toFixed(4)+'"><span>±'+l+'%</span>'+
     '<b>Buy ↑ $'+escape(exact(BigInt(r[l].up_usdt_raw),6))+' <em>USDT</em></b>'+
     '<b>Sell ↓ $'+escape(exact(BigInt(r[l].down_usdt_raw),6))+' <em>USDT</em></b></div>':'').join("");
   tooltip.style.left=Math.max(6,Math.min(W-tooltip.offsetWidth-6,px>W/2?px-tooltip.offsetWidth-14:px+14))+"px";
   tooltip.style.top=Math.max(4,Math.min(top+8,H-tooltip.offsetHeight-8))+"px";
   tooltip.hidden=false;
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
  if(!history){host.innerHTML='<p class="empty">Confirmed balance history is not available yet.</p>';return;}
  if(!history.points.length){host.innerHTML='<p class="empty">The address has no WGNK movements before the verified snapshot yet.</p>';return;}
  const rows=history.points.map(p=>({...p,buy:BigInt(p.bought_raw),sell:BigInt(p.sold_raw),balance:BigInt(p.balance_raw)}));
  const W=Math.max(280,host.clientWidth),small=W<500,H=small?300:310;
  const left=small?54:76,right=small?58:80,top=48,bottom=H-38,plot=W-left-right;
  const volumeHigh=ceiling(rows.reduce((max,r)=>r.buy>max?(r.buy>r.sell?r.buy:r.sell):r.sell>max?r.sell:max,4000000000n));
  const balanceHigh=ceiling(rows.reduce((max,r)=>r.balance>max?r.balance:max,4000000000n));
  const slot=plot/rows.length,x=i=>left+(i+.5)*slot;
  const y=(raw,high)=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  const names={buy:"Buys",sell:"Sells"};
  let svg='<svg class="address-chart" viewBox="0 0 '+W+' '+H+'" role="img" tabindex="0" aria-label="WGNK buys and sells by day — green and red bars, scale on the left. WGNK balance — blue line, scale on the right. Arrows select a day.">';
  svg+='<text class="chart-unit" x="'+left+'" y="15">Volume / day</text><text class="chart-unit" x="'+left+'" y="33">WGNK</text><text class="chart-unit balance-axis" x="'+(W-right)+'" y="15" text-anchor="end">Balance</text><text class="chart-unit balance-axis" x="'+(W-right)+'" y="33" text-anchor="end">WGNK</text>';
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
   const time=new Date(Number(host.dataset.snapshotTs||history.ts)*1000).toLocaleTimeString('en-GB',{timeZone:history.timezone,hour:'2-digit',minute:'2-digit'});
   tooltip.innerHTML='<span>'+fullDate(row.date)+(onSnapshot?' · until '+escape(time):'')+'</span><strong class="balance-value">'+escape(exact(row.balance,9))+' <small>WGNK</small></strong><span>Balance '+(onSnapshot?'at snapshot':'at end of day')+'</span><div class="address-tooltip-sides">'+['buy','sell'].map(kind=>'<div data-trade-kind="'+kind+'"><span class="trade-key '+kind+'">'+names[kind]+'</span><b>'+escape(exact(row[kind],9))+' WGNK</b><small>'+(kind==='buy'?row.buys_count:row.sales_count).toLocaleString('en-US')+' exec.</small><small class="address-day-price">Average price: <b>'+escape(formatPrice(row[kind]>0n&&row[kind==='buy'?'buy_quote_raw':'sale_quote_raw']!=null?BigInt(row[kind==='buy'?'buy_quote_raw':'sale_quote_raw'])*10n**15n/row[kind]:null))+'</b> USDT</small></div>').join('')+'</div>';
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
 function renderGroups(host,history,kinds){
  if(!history?.ready||!history.points?.length){host.innerHTML='<p class="empty">Category history is not confirmed yet. These are not zero balances.</p>';return;}
  const keys=Object.keys(kinds),rows=history.points.map(p=>({...p,values:Object.fromEntries(keys.map(k=>[k,BigInt(p[k+'_raw'])]))}));
  const W=Math.max(280,host.clientWidth),small=W<500,H=small?320:350,left=small?52:76,right=18,top=30,bottom=H-40,plot=W-left-right;
  const high=ceiling(rows.reduce((max,r)=>keys.reduce((m,k)=>r.values[k]>m?r.values[k]:m,max),4000000000n));
  const x=i=>rows.length===1?left+plot/2:left+i*plot/(rows.length-1),y=raw=>bottom-Number(raw*1000000n/high)/1000000*(bottom-top);
  let svg='<svg class="holder-history-chart" viewBox="0 0 '+W+' '+H+'" role="img" tabindex="0" aria-label="Balance of the four WGNK categories since the bridge was created. Categories as of each date. Arrows select a day."><text class="chart-unit" x="'+left+'" y="15">Balance · WGNK</text>';
  for(let i=0;i<=4;i++){
   const raw=high*BigInt(i)/4n,py=y(raw);
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text x="'+(left-9)+'" y="'+(py+4)+'" text-anchor="end">'+escape(axis(raw,9))+'</text>';
  }
  const last=rows.length-1;
  keys.forEach(key=>{
   svg+='<path class="holder-history-line holder-'+key+'" data-holder-kind="'+key+'" d="'+rows.map((r,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(r.values[key]).toFixed(2)).join(' ')+'"><title>'+escape(kinds[key].label)+'</title></path><circle class="holder-history-last holder-'+key+'" data-holder-kind="'+key+'" data-balance-raw="'+rows[last].values[key]+'" cx="'+x(last)+'" cy="'+y(rows[last].values[key])+'" r="3"/>';
  });
  const ticks=Math.min(rows.length,small?3:7);
  for(let i=0;i<ticks;i++){
   const index=ticks===1?0:Math.round(i*last/(ticks-1));
   svg+='<text x="'+x(index)+'" y="'+(bottom+25)+'" text-anchor="'+(ticks===1?'middle':i===0?'start':i===ticks-1?'end':'middle')+'">'+label(rows[index].date)+'</text>';
  }
  svg+='<g class="chart-cursor" visibility="hidden"><line class="chart-crosshair" y1="'+top+'" y2="'+bottom+'"/>'+keys.map(key=>'<circle class="holder-history-dot holder-'+key+'" data-holder-kind="'+key+'" r="4"/>').join('')+'</g><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip holder-history-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector('svg'),cursor=host.querySelector('.chart-cursor'),tooltip=host.querySelector('.chart-tooltip');
  let selected=last;
  const show=index=>{
   selected=Math.max(0,Math.min(last,index));const row=rows[selected],px=x(selected),total=BigInt(row.total_raw);
   cursor.setAttribute('visibility','visible');const line=cursor.querySelector('line');line.setAttribute('x1',px);line.setAttribute('x2',px);
   keys.forEach(key=>{const dot=cursor.querySelector('[data-holder-kind="'+key+'"]');dot.setAttribute('cx',px);dot.setAttribute('cy',y(row.values[key]));});
   const time=new Date(history.ts*1000).toLocaleTimeString('en-GB',{timeZone:history.timezone,hour:'2-digit',minute:'2-digit'});
   tooltip.innerHTML='<span class="holder-history-date">'+fullDate(row.date)+(selected===last?' · until '+escape(time):' · end of day')+'</span><div class="holder-history-values">'+keys.map(key=>{
    const raw=row.values[key],percent=total?(raw>0n&&raw*100n<total?'< 1':(raw*100n/total).toString())+'%':'—';
    return '<div class="holder-'+key+'" data-holder-kind="'+key+'" data-balance-raw="'+raw+'"><span><i></i>'+escape(kinds[key].label)+'</span><b>'+escape(exact(raw,9))+'</b><small>'+escape(percent)+' · addresses: '+row.addresses[key].toLocaleString('en-US')+'</small></div>';
   }).join('')+'</div><div class="holder-history-total">Total outside pools <b>'+escape(exact(total,9))+' WGNK</b></div>';
   tooltip.dataset.date=row.date;tooltip.dataset.totalRaw=row.total_raw;tooltip.hidden=false;
   tooltip.style.left=Math.max(2,Math.min(W-tooltip.offsetWidth-2,px>W/2?px-tooltip.offsetWidth-12:px+12))+'px';
   tooltip.style.top=Math.max(2,Math.min(top+8,H-tooltip.offsetHeight-5))+'px';
  };
  const hide=()=>{cursor.setAttribute('visibility','hidden');tooltip.hidden=true;};
  const pointer=event=>{const box=root.getBoundingClientRect(),px=(event.clientX-box.left)*W/box.width;show(rows.length===1?0:Math.round((px-left)/plot*last));};
  root.addEventListener('pointermove',pointer);root.addEventListener('pointerdown',pointer);root.addEventListener('pointerleave',hide);
  root.addEventListener('focus',()=>show(selected));root.addEventListener('blur',hide);
  root.addEventListener('keydown',event=>{
   if(['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))event.preventDefault();
   if(event.key==='ArrowLeft')show(selected-1);if(event.key==='ArrowRight')show(selected+1);
   if(event.key==='Home')show(0);if(event.key==='End')show(last);if(event.key==='Escape')hide();
  });
 }
 function priceTick(raw){
  const cents=BigInt(raw)/10000000000n;
  return (cents/100n).toLocaleString("en-US")+"."+(cents%100n).toString().padStart(2,"0");
 }
 const priceRange=(from,to)=>priceTick(from)+"–"+priceTick(to);
 function formatShare(raw,total){
  const n=BigInt(raw),d=BigInt(total);if(!d)return "—";
  const tenths=n*1000n/d;
  return n>0n&&!tenths?"< 0.1%":(tenths/10n).toString()+(tenths%10n?"."+tenths%10n:"")+"%";
 }
 function bandVolumeLabel(raw){
  const n=BigInt(raw);
  for(const [unit,suffix] of [[1000000000000000n,"M"],[1000000000000n,"K"]]){
   if(n<unit)continue;
   const hundredths=(n*100n+unit/2n)/unit,fraction=(hundredths%100n).toString().padStart(2,"0").replace(/0+$/,"");
   return (hundredths/100n).toLocaleString("en-US")+(fraction?"."+fraction:"")+" "+suffix;
  }
  return exact(n,9);
 }
 function bandVolumeCeiling(raw){
  const required=(BigInt(raw)+3n)/4n;if(required<=1n)return 4n;
  const power=10n**BigInt(required.toString().length-1);
  const step=[100n,125n,150n,200n,250n,500n,1000n].map(factor=>(factor*power+99n)/100n).find(value=>value>=required);
  return step*4n;
 }
 function renderPriceBands(host,points,{side,bounds,stepRaw,maximum,countMaximum=0,dayBands=null,bridgeBands=null,sparse=false}){
  if(!points.length){host.innerHTML='<p class="empty">'+(side==="buy"?"No buys":"No sells")+' with a confirmed address yet.</p>';return;}
  const lookup=new Map(points.map(p=>[p.from_price_raw,p]));
  const dayLookup=dayBands===null?null:new Map(dayBands.map(row=>[row.from_price_raw,row.days]));
  const bridgeLookup=bridgeBands===null?null:new Map(bridgeBands.map(row=>[row.from_price_raw,row]));
  const rows=bounds.map(from=>({...(lookup.get(from)||{from_price_raw:from,to_price_raw:String(BigInt(from)+BigInt(stepRaw)),volume_raw:"0",quote_raw:"0",swaps:0,price_raw:null}),days:dayLookup===null?null:dayLookup.get(from)||0,bridge:bridgeLookup===null?null:bridgeLookup.get(from)||{in_raw:"0",out_raw:"0"}}));
  const total=points.reduce((sum,row)=>sum+BigInt(row.volume_raw),0n);
  const W=Math.max(280,host.clientWidth),H=W<500?340:360,left=W<500?66:68,right=54,top=44,bottom=H-68,plot=W-left-right;
  const high=bandVolumeCeiling(maximum),slot=plot/rows.length,barWidth=Math.max(1,Math.min(80,slot*.68));
  const countHigh=ceiling(points.reduce((max,row)=>BigInt(row.swaps)>max?BigInt(row.swaps):max,BigInt(countMaximum)));
  const x=i=>left+slot*(i+.5),y=raw=>bottom-Number(BigInt(raw)*1000000n/high)/1000000*(bottom-top);
  const countY=count=>bottom-Number(BigInt(count)*1000000n/countHigh)/1000000*(bottom-top);
  const labelStride=Math.max(1,Math.ceil(rows.length/Math.max(1,Math.floor(plot/34))));
  let svg='<svg class="price-histogram '+side+'" viewBox="0 0 '+W+' '+H+'" role="img" tabindex="0" aria-label="'+(side==="buy"?"Buys":"Sells")+': WGNK volume as bars, scale on the left. Trade count as a line, scale on the right. The tooltip shows the trade count, days at the level and bridge volume for those days. Arrows select a price range.">';
  svg+='<text class="chart-unit" x="'+left+'" y="17">Volume · WGNK</text><text class="chart-unit price-count-axis" x="'+(W-right)+'" y="17" text-anchor="end">Trades</text>';
  for(let i=0;i<=4;i++){
   const py=bottom-i*(bottom-top)/4;
   svg+='<line class="chart-grid" x1="'+left+'" x2="'+(W-right)+'" y1="'+py+'" y2="'+py+'"/><text x="'+(left-9)+'" y="'+(py+4)+'" text-anchor="end">'+escape(bandVolumeLabel(high*BigInt(i)/4n))+'</text><text class="price-count-axis" x="'+(W-right+9)+'" y="'+(py+4)+'">'+escape((countHigh*BigInt(i)/4n).toLocaleString('en-US'))+'</text>';
  }
  rows.forEach((row,i)=>{
   const py=y(row.volume_raw);
   if(BigInt(row.volume_raw)>0n){
    svg+='<rect class="price-volume" data-kind="'+side+'" data-from="'+row.from_price_raw+'" data-to="'+row.to_price_raw+'" data-raw="'+row.volume_raw+'" x="'+(x(i)-barWidth/2)+'" y="'+py+'" width="'+barWidth+'" height="'+(bottom-py)+'" rx="3"/>';
    if(slot>=52)svg+='<text class="price-bar-value" x="'+x(i)+'" y="'+(Math.min(py,countY(row.swaps))-10)+'" text-anchor="middle">'+escape(bandVolumeLabel(row.volume_raw))+'</text>';
   }
   if(i%labelStride===0||i===rows.length-1)svg+='<text class="price-band-label" x="'+x(i)+'" y="'+(bottom+20)+'" text-anchor="middle"><tspan x="'+x(i)+'">'+escape(priceTick(row.from_price_raw))+'</tspan><tspan x="'+x(i)+'" dy="14">–'+escape(priceTick(row.to_price_raw))+'</tspan></text>';
  });
  svg+='<path class="price-count-line" d="'+rows.map((row,i)=>(i?'L':'M')+x(i).toFixed(2)+','+countY(row.swaps).toFixed(2)).join(' ')+'"/>';
  rows.forEach((row,i)=>{
   svg+='<circle class="price-count-point" data-kind="'+side+'" data-from="'+row.from_price_raw+'" data-to="'+row.to_price_raw+'" data-swaps="'+row.swaps+'" data-days="'+(row.days??'')+'" cx="'+x(i)+'" cy="'+countY(row.swaps)+'" r="3.5"/>';
  });
  svg+='<text class="chart-unit" x="'+(left+plot/2)+'" y="'+(H-6)+'" text-anchor="middle">'+(sparse?'Ranges with trades · ':'')+'Price · USDT per 1 WGNK</text>';
  svg+='<rect class="price-band-selection" visibility="hidden" y="'+top+'" height="'+(bottom-top)+'" width="'+slot+'" rx="4"/><rect class="chart-hit" x="'+left+'" y="'+top+'" width="'+plot+'" height="'+(bottom-top)+'" fill="transparent"/></svg><div class="chart-tooltip price-band-tooltip" role="status" hidden></div>';
  host.innerHTML=svg;
  const root=host.querySelector('svg'),selection=host.querySelector('.price-band-selection'),tooltip=host.querySelector('.chart-tooltip');
  const peak=points.reduce((best,row)=>BigInt(row.volume_raw)>BigInt(best.volume_raw)?row:best);
  let selected=Math.max(0,rows.findIndex(row=>row.from_price_raw===peak.from_price_raw));
  function show(index){
   selected=Math.max(0,Math.min(rows.length-1,index));
   const row=rows[selected],px=x(selected);
   selection.setAttribute('x',left+slot*selected);selection.setAttribute('visibility','visible');
   host.querySelectorAll('.price-volume').forEach(bar=>bar.classList.toggle('is-selected',bar.dataset.from===row.from_price_raw));
   host.querySelectorAll('.price-count-point').forEach(dot=>dot.classList.toggle('is-selected',dot.dataset.from===row.from_price_raw));
   tooltip.innerHTML='<strong>'+escape(priceRange(row.from_price_raw,row.to_price_raw))+' <small>USDT</small></strong><span>Price per 1 WGNK · up to the upper bound</span><div class="price-band-stats"><div class="price-count-stat"><b>'+row.swaps.toLocaleString('en-US')+'</b><span>Trades</span></div><div><b>'+(row.days===null?'—':row.days.toLocaleString('en-US'))+'</b><span>Days at level</span></div></div><strong>'+escape(exact(row.volume_raw,9))+' <small>WGNK</small></strong><span>'+escape(formatShare(row.volume_raw,total))+' of total '+(side==='buy'?'buy volume':'sell volume')+'</span><span>Amount: '+escape(exact(row.quote_raw,6))+' USDT</span><span>Average price: '+escape(formatPrice(row.price_raw))+' USDT</span>';
   tooltip.innerHTML+='<div class="price-bridge-stats"><span>Bridge on these days · WGNK</span>'+(row.bridge?
    '<div><span>Inflow</span><b class="bridge-in">'+escape(exact(row.bridge.in_raw,9))+'</b></div><div><span>Outflow</span><b class="bridge-out">'+escape(exact(row.bridge.out_raw,9))+'</b></div>':
    '<span>Data not confirmed yet</span>')+'</div>';
   tooltip.dataset.from=row.from_price_raw;tooltip.dataset.swaps=String(row.swaps);tooltip.dataset.days=row.days===null?'':String(row.days);
   tooltip.dataset.bridgeIn=row.bridge?.in_raw??'';tooltip.dataset.bridgeOut=row.bridge?.out_raw??'';
   tooltip.hidden=false;
   tooltip.style.left=Math.max(6,Math.min(W-tooltip.offsetWidth-6,px>W/2?px-tooltip.offsetWidth-12:px+12))+'px';
   tooltip.style.top=Math.max(6,Math.min(24,H-tooltip.offsetHeight-6))+'px';
  }
  const hide=()=>{selection.setAttribute('visibility','hidden');tooltip.hidden=true;host.querySelectorAll('.is-selected').forEach(bar=>bar.classList.remove('is-selected'));};
  const pointer=event=>{const box=root.getBoundingClientRect();show(Math.floor(((event.clientX-box.left)*W/box.width-left)/slot));};
  root.addEventListener('pointermove',pointer);root.addEventListener('pointerdown',pointer);root.addEventListener('pointerleave',hide);
  root.addEventListener('focus',()=>show(selected));root.addEventListener('blur',hide);
  root.addEventListener('keydown',event=>{
   if(['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))event.preventDefault();
   if(event.key==='ArrowLeft')show(selected-1);if(event.key==='ArrowRight')show(selected+1);
   if(event.key==='Home')show(0);if(event.key==='End')show(rows.length-1);if(event.key==='Escape')hide();
  });
 }
 return {render,renderMarket,renderLiquidity,renderAddress,renderGroups,renderPriceBands,priceRange,formatShare,exact,formatPrice};
})();
