/* Forecast: deterministic rules over verified data plus an optional external model.
   The API key never leaves the browser: calls go directly to the model provider,
   and the model itself may fetch public site data through declared tools. */
(()=>{
 const state={data:null,loading:false,request:0,abort:null};
 const KEY_STORAGE='gonka-forecast-llm-key';
 const OPENBROKER='https://api.openbroker.gonka.gg/v1/chat/completions';
 const MODEL='deepseek-ai/DeepSeek-V4-Flash-0731';
 const MAX_TOKENS=4000;
 const MAX_TOOL_ROUNDS=6;
 const direction={up:'▲ рост',down:'▼ падение',flat:'— нейтрально'};
 const usd=raw=>(Number(raw)/1e6).toLocaleString('ru-RU',{maximumFractionDigits:0});
 const wgnk=raw=>(Number(raw)/1e9).toLocaleString('ru-RU',{maximumFractionDigits:0});
 function renderVerdict(data){
  const verdict=data.verdict;
  if(!verdict){el('forecast-verdict').innerHTML='<p class="empty">Снимок прогноза ещё не готов: ждём верифицированную историю и порох.</p>';return;}
  el('forecast-verdict').innerHTML='<strong class="'+verdict.direction+'">'+esc(direction[verdict.direction])+
   '</strong><span class="forecast-score">балл правил '+verdict.score.toFixed(3)+
   ' · от −1 до +1 · снимок #'+count(data.snapshot?.height||0)+' · '+clock(data.snapshot?.ts||data.now)+' '+date(data.snapshot?.ts||data.now)+'</span>';
 }
 function renderRules(data){
  el('forecast-rules').innerHTML=(data.rules||[]).map(rule=>
   '<div class="rule"><div><strong>'+esc(rule.name)+'</strong><small>'+esc(rule.explanation)+'</small></div>'+
   '<span class="rule-direction '+rule.direction+'">'+esc(direction[rule.direction].replace('▲ ','').replace('▼ ','').replace('— ',''))+'</span>'+
   '<span class="rule-weight">вес '+rule.weight+'</span></div>').join('')||'<p class="empty">Правила ещё не посчитаны.</p>';
 }
 function renderPowder(data){
  const powder=data.powder;
  if(!powder?.ready){el('powder-buy').innerHTML=el('powder-sell').innerHTML='<p class="empty">Порох ещё собирается…</p>';return;}
  const buyers=(powder.buyers||[]).slice(0,8).map(row=>
   '<li><button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button>'+
   '<span><strong>'+usd(row.own_raw)+'</strong> USDT'+(Number(row.chain_raw)>0?' <small>+ цепочки '+usd(row.chain_raw)+'</small>':'')+'</span></li>').join('');
  const sellers=(powder.sellers||[]).slice(0,8).map(row=>
   '<li><button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button>'+
   '<span><strong>'+wgnk(row.wgnk_raw)+'</strong> WGNK'+(Number(row.gnk_raw)>0?' <small>+ GNK '+wgnk(row.gnk_raw)+'</small>':'')+'</span></li>').join('');
  const t=powder.totals;
  el('powder-buy').innerHTML='<h3>Порох покупателей</h3><div class="total">'+usd(t.buy_own_raw)+
   ' <span class="unit">USDT-экв.</span></div><p class="unit">собственный + '+usd(t.buy_chain_raw)+' в цепочках финансирования</p>'+
   '<ul class="powder-list">'+(buyers||'<li><small>Нет адресов над порогом.</small></li>')+'</ul>';
  el('powder-sell').innerHTML='<h3>Запасы продавцов</h3><div class="total">'+wgnk(t.sell_wgnk_raw)+
   ' <span class="unit">WGNK</span></div><p class="unit">+ '+wgnk(t.sell_gnk_raw)+' GNK у мост-источников · эскроу '+wgnk(t.escrow_raw)+' GNK</p>'+
   '<ul class="powder-list">'+(sellers||'<li><small>Нет адресов над порогом.</small></li>')+'</ul>';
 }

 // ---- Tools the model may call; every fetch is same-origin public API data ----
 const TOOLS=[
  {type:'function',function:{name:'forecast_snapshot',description:'Полный снимок прогноза: пять правил с числами, сухой порох покупателей (адреса, собственные и цепочечные стейблкоины, покрытие балансов), запасы продавцов WGNK/GNK.',
   parameters:{type:'object',properties:{},required:[]}}},
  {type:'function',function:{name:'daily_history',description:'Дневная история: средневзвешенная цена USDT, объёмы покупок и продаж WGNK и глубина ликвидности ±2% пула 30 б.п. по дням.',
   parameters:{type:'object',properties:{days:{type:'integer',description:'Сколько последних дней вернуть, до 120',minimum:1,maximum:120}},required:[]}}},
  {type:'function',function:{name:'trader_leaders',description:'Крупнейшие покупатели или продавцы WGNK за всю историю, с группами вероятных адресов одного участника.',
   parameters:{type:'object',properties:{side:{type:'string',enum:['buy','sell']},limit:{type:'integer',minimum:1,maximum:50}},required:['side']}}},
  {type:'function',function:{name:'bridge_feed',description:'Последние операции моста: выпуск WGNK в Ethereum (минты) и вывод в Gonka (бёрны) с суммами и временем.',
   parameters:{type:'object',properties:{limit:{type:'integer',minimum:1,maximum:100}},required:[]}}}];

 async function runTool(call){
  const args={};
  try{Object.assign(args,JSON.parse(call.function.arguments||'{}'));}catch{}
  switch(call.function.name){
   case 'forecast_snapshot':{
    const f=await (await fetch('/api/mints/forecast')).json();
    const powder=f.powder||{};
    return JSON.stringify({price_usdt:f.price_raw?Number(f.price_raw)/1e12:null,
     verdict:f.verdict,rules:(f.rules||[]).map(r=>({name:r.name,score:r.score,weight:r.weight,explanation:r.explanation})),
     powder:{coverage:powder.balance_coverage,totals:powder.totals,
      top_buyers:(powder.buyers||[]).slice(0,10).map(b=>({address:b.address,own_usdt:+(Number(b.own_raw)/1e6).toFixed(0),chain_usdt:+(Number(b.chain_raw)/1e6).toFixed(0)})),
      top_sellers:(powder.sellers||[]).slice(0,10).map(s=>({address:s.address,wgnk:+(Number(s.wgnk_raw)/1e9).toFixed(0),gnk:+(Number(s.gnk_raw)/1e9).toFixed(0)}))}});
   }
   case 'daily_history':{
    const days=Math.min(120,Math.max(1,args.days||30));
    const f=await (await fetch('/api/mints/flows?side=all&limit=1')).json();
    const pool30=(f.pools||[]).find(p=>p.fee===3000);
    const band=(f.liquidity_band&&pool30?f.liquidity_band[pool30.address]:null)||[];
    const byDate=new Map(band.map(b=>[b.date,b]));
    return JSON.stringify((f.daily||[]).slice(-days).map(d=>{
     const b=byDate.get(d.date)||{};
     return {date:d.date,price_usdt:+(Number(d.price_raw)/1e12).toFixed(6),
      bought_wgnk:+(Number(d.bought_raw)/1e9).toFixed(0),sold_wgnk:+(Number(d.sold_raw)/1e9).toFixed(0),
      buy_usdt:+(Number(d.buy_quote_raw)/1e6).toFixed(0),sell_usdt:+(Number(d.sale_quote_raw)/1e6).toFixed(0),
      band_wgnk:b.wgnk_raw?+(Number(b.wgnk_raw)/1e9).toFixed(0):null,
      band_usdt:b.usdt_raw?+(Number(b.usdt_raw)/1e6).toFixed(0):null};}));
   }
   case 'trader_leaders':{
    const side=args.side==='sell'?'sell':'buy';
    const limit=Math.min(50,Math.max(1,args.limit||15));
    const l=await (await fetch('/api/mints/leaders?grouped=true')).json();
    return JSON.stringify((l[side==='buy'?'buyers':'sellers']||[]).slice(0,limit).map(r=>({
     address:r.group?r.group.addresses:r.address,volume_wgnk:+(Number(r.volume_raw)/1e9).toFixed(0),
     quote_usdt:+(Number(r.quote_raw)/1e6).toFixed(0),swaps:r.swaps,
     group:r.group?{size:r.group.addresses.length,evidence:r.group.evidence.map(e=>e.label)}:null})));
   }
   case 'bridge_feed':{
    const limit=Math.min(100,Math.max(1,args.limit||30));
    const b=await (await fetch('/api/mints/bridge?minimum=1000&limit='+limit)).json();
    return JSON.stringify((b.items||[]).map(e=>({kind:e.kind==='bridge_mint'?'mint(WGNK в Ethereum)':'burn(вывод в Gonka)',
     address:e.address,amount_wgnk:+(Number(e.amount_raw)/1e9).toFixed(0),
     time:new Date(e.ts*1000).toISOString().slice(0,16).replace('T',' ')})));
   }
   default:
    return JSON.stringify({error:'unknown tool '+call.function.name});
  }
 }

 const SYSTEM_PROMPT='Ты аналитик рынка токена WGNK (Gonka). У тебя есть инструменты для запроса публичных данных сайта: снимок прогноза с порохом и запасами, дневная история цен и ликвидности, рейтинги трейдеров, лента моста. Запроси то, что нужно для обоснованного вывода (обычно 2-4 вызова), затем дай развёрнутый прогноз на русском: 1) строка ВЕРДИКТ: рост или падение или нейтрально + уверенность в процентах; 2) 3-6 аргументов с конкретными числами из полученных данных; 3) главные риски сценария; 4) что проверить через 24 часа. Не выдумывай числа, которых нет в данных.';

 const llm={busy:false};
 async function callModel(key,messages,tools){
  const response=await fetch(OPENBROKER,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
   body:JSON.stringify({model:MODEL,temperature:0,max_tokens:MAX_TOKENS,messages,
    tools:tools||undefined,tool_choice:tools?'auto':undefined})});
  if(!response.ok){
   let detail='HTTP '+response.status;let concurrent=false;
   try{const payload=await response.json();
    const message=payload.error?.message||String(payload.error||payload.detail||'');
    detail+=': '+message;concurrent=/concurrent/i.test(message);}catch{}
   if(response.status===429&&!concurrent)detail+=' · лимит запросов ключа OpenBroker: подождите минуту и повторите, либо проверьте квоту/тариф ключа';
   if(response.status===401)detail='Ключ не принят (401): проверьте, что скопировали ключ OpenBroker целиком';
   const error=new Error(detail);error.concurrent=concurrent;error.status=response.status;throw error;
  }
  const choice=(await response.json()).choices?.[0];
  return {content:(choice?.message?.content||'').trim(),tool_calls:choice?.message?.tool_calls||null};
 }
 async function askModel(){
  if(llm.busy){el('forecast-llm-status').textContent='Запрос уже выполняется…';return;}
  const key=el('forecast-llm-key').value.trim();
  const status=el('forecast-llm-status');
  if(!key){status.textContent='Введите ключ API.';return;}
  try{localStorage.setItem(KEY_STORAGE,key);}catch{}
  const data=state.data;
  if(!data?.ready){status.textContent='Снимок прогноза ещё не готов.';return;}
  llm.busy=true;el('forecast-llm-ask').disabled=true;
  try{
   const messages=[{role:'system',content:SYSTEM_PROMPT},
    {role:'user',content:'Дай развёрнутый прогноз WGNK. Текущий вердикт правил сайта: '+
      (data.verdict?.label||'?')+' ('+(data.verdict?.score??0).toFixed(3)+'). Собери данные инструментами и обоснуй вывод.'}];
   let content='',rounds=0;const toolNotes=[];
   let answer;
   while(rounds++<MAX_TOOL_ROUNDS){
    answer=await callModel(key,messages,TOOLS);
    if(answer.tool_calls&&answer.tool_calls.length){
     messages.push({role:'assistant',content:answer.content||'',tool_calls:answer.tool_calls});
     for(const call of answer.tool_calls){
      status.textContent='Агент запрашивает: '+call.function.name+'…';
      const result=await runTool(call);
      messages.push({role:'tool',tool_call_id:call.id,content:String(result).slice(0,60000)});
      toolNotes.push(call.function.name);
     }
     continue;
    }
    content=answer.content;
    break;
   }
   if(!content){
    // Provider may not support tool calls: one rich request without tools.
    status.textContent='Инструменты не поддержаны, спрашиваем одним запросом…';
    const f=await (await fetch('/api/mints/forecast')).json();
    const flows=await (await fetch('/api/mints/flows?side=all&limit=1')).json();
    const rich=JSON.stringify({verdict:f.verdict,rules:(f.rules||[]).map(r=>[r.name,r.score,r.explanation]),
     powder:f.powder&&{totals:f.powder.totals,coverage:f.powder.balance_coverage,
      buyers:(f.powder.buyers||[]).slice(0,10),sellers:(f.powder.sellers||[]).slice(0,10)},
     daily_30d:(flows.daily||[]).slice(-30).map(d=>[d.date,+(Number(d.price_raw)/1e12).toFixed(6),+(Number(d.bought_raw)/1e9).toFixed(0),+(Number(d.sold_raw)/1e9).toFixed(0)])}).slice(0,50000);
    content=(await callModel(key,[{role:'system',content:SYSTEM_PROMPT},{role:'user',content:rich}],null)).content;
    toolNotes.push('без инструментов');
   }
   el('forecast-llm-reply').hidden=false;
   el('forecast-llm-reply').textContent=content||'(пустой ответ)';
   status.textContent='Готово · прямой вызов из браузера · модель '+MODEL+
    (toolNotes.length?' · данные: '+toolNotes.join(', '):'');
  }finally{llm.busy=false;el('forecast-llm-ask').disabled=false;}
 }
 el('forecast-llm-ask').addEventListener('click',()=>askModel().catch(error=>{el('forecast-llm-status').textContent='Ошибка: '+error.message;}));
 el('forecast-llm-key').addEventListener('keydown',event=>{if(event.key==='Enter')el('forecast-llm-ask').click();});
 try{el('forecast-llm-key').value=localStorage.getItem(KEY_STORAGE)||'';}catch{}
 window.addEventListener('gonka:view',()=>{if(!el('forecast-view').hidden)refresh();});
 setInterval(()=>{if(!document.hidden&&!el('forecast-view').hidden)refresh();},60000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!el('forecast-view').hidden)refresh();});
 async function refresh(){
  if(state.loading)return;
  state.abort?.abort();
  const id=++state.request,controller=state.abort=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  state.loading=true;el('forecast-view').setAttribute('aria-busy','true');
  try{
   const response=await fetch('/api/mints/forecast',{signal:controller.signal});
   if(!response.ok)throw new Error('HTTP '+response.status);
   const data=await response.json();if(id!==state.request)return;
   state.data=data;
   el('forecast-status').textContent=data.ready?'':'Снимок прогноза ещё собирается; покажем правила, как только история и порох будут проверены.';
   renderVerdict(data);renderRules(data);renderPowder(data);
  }catch(error){if(id===state.request)el('forecast-status').textContent='Не удалось обновить прогноз. Повторим автоматически.';}
  finally{clearTimeout(timer);if(id===state.request){state.loading=false;el('forecast-view').setAttribute('aria-busy','false');}}
 }
 if(!el('forecast-view').hidden)refresh();
})();
