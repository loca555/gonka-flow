/* Forecast: deterministic rules over verified data plus an optional external model.
   The API key never leaves the browser: calls go directly to the model provider,
   and the model itself may fetch public site data through declared tools. */
(()=>{
 const state={data:null,loading:false,request:0,abort:null};
 const KEY_STORAGE='gonka-forecast-llm-key';
 const MODEL_STORAGE='gonka-forecast-llm-model';
 const OPENBROKER='https://api.openbroker.gonka.gg/v1/chat/completions';
 const MODEL='deepseek-ai/DeepSeek-V4-Flash-0731';
 const MAX_TOKENS=33000;
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
  const main=n=>n>1?' <small title="Вероятная группа одного участника; показан основной адрес">· основной из '+n+'</small>':'';
  const buyers=(powder.buyers||[]).slice(0,8).map(row=>
   '<li><button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button>'+
   '<span><strong>'+usd(row.own_raw)+'</strong> USDT'+(Number(row.chain_raw)>0?' <small>+ цепочки '+usd(row.chain_raw)+'</small>':'')+main(row.group_size||1)+'</span></li>').join('');
  const sellers=(powder.sellers||[]).slice(0,8).map(row=>
   '<li><button class="address-button" data-flow-address="'+esc(row.address)+'" title="'+esc(row.address)+'">'+esc(short(row.address,8))+'</button>'+
   '<span><strong>'+wgnk(row.wgnk_raw)+'</strong> WGNK'+(Number(row.gnk_raw)>0?' <small>+ GNK '+wgnk(row.gnk_raw)+'</small>':'')+main(row.group_size||1)+'</span></li>').join('');
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

 async function getJSON(url){
  let response=null,text='';
  for(let attempt=0;attempt<3;attempt++){
   if(attempt)await new Promise(done=>setTimeout(done,attempt*3000));
   try{
    response=await fetch(url);
    text=await response.text();
    if(response.ok&&!text.trim().startsWith('<'))return JSON.parse(text);
   }catch(error){response=null;}
  }
  if(response&&!response.ok&&response.status>=500)
   throw new Error('сайт перезапускается (HTTP '+response.status+') — подождите минуту и повторите');
  throw new Error('данные сайта не ответили — подождите минуту и повторите');
 }
 async function runTool(call){
  const args={};
  try{Object.assign(args,JSON.parse(call.function.arguments||'{}'));}catch{}
  switch(call.function.name){
   case 'forecast_snapshot':{
    const f=await getJSON('/api/mints/forecast');
    const powder=f.powder||{};
    return JSON.stringify({price_usdt:f.price_raw?Number(f.price_raw)/1e12:null,
     verdict:f.verdict,rules:(f.rules||[]).map(r=>({name:r.name,score:r.score,weight:r.weight,explanation:r.explanation})),
     powder:{coverage:powder.balance_coverage,totals:powder.totals,
      top_buyers:(powder.buyers||[]).slice(0,10).map(b=>({address:b.address,own_usdt:+(Number(b.own_raw)/1e6).toFixed(0),chain_usdt:+(Number(b.chain_raw)/1e6).toFixed(0)})),
      top_sellers:(powder.sellers||[]).slice(0,10).map(s=>({address:s.address,wgnk:+(Number(s.wgnk_raw)/1e9).toFixed(0),gnk:+(Number(s.gnk_raw)/1e9).toFixed(0)}))}});
   }
   case 'daily_history':{
    const days=Math.min(120,Math.max(1,args.days||30));
    const f=await getJSON('/api/mints/flows?side=all&limit=1');
    const pool30=(f.pools||[]).find(p=>p.fee===3000);
    const bands=f.liquidity_bands&&pool30?f.liquidity_bands[pool30.address]:null;
    const band=(bands&&bands['2'])||[];
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
    const l=await getJSON('/api/mints/leaders?grouped=true');
    return JSON.stringify((l[side==='buy'?'buyers':'sellers']||[]).slice(0,limit).map(r=>({
     address:r.group?r.group.addresses:r.address,volume_wgnk:+(Number(r.volume_raw)/1e9).toFixed(0),
     quote_usdt:+(Number(r.quote_raw)/1e6).toFixed(0),swaps:r.swaps,
     group:r.group?{size:r.group.addresses.length,evidence:r.group.evidence.map(e=>e.label)}:null})));
   }
   case 'bridge_feed':{
    const limit=Math.min(100,Math.max(1,args.limit||30));
    const b=await getJSON('/api/mints/bridge?minimum=1000&limit='+limit);
    return JSON.stringify((b.items||[]).map(e=>({kind:e.kind==='bridge_mint'?'mint(WGNK в Ethereum)':'burn(вывод в Gonka)',
     address:e.address,amount_wgnk:+(Number(e.amount_raw)/1e9).toFixed(0),
     time:new Date(e.ts*1000).toISOString().slice(0,16).replace('T',' ')})));
   }
   default:
    return JSON.stringify({error:'unknown tool '+call.function.name});
  }
 }

 const SYSTEM_PROMPT=[
  'Ты исследователь рынка токена WGNK (Gonka). Твоя задача — саммари рынка в стиле независимого исследовательского отчёта, а не прогноз.',
  'Сначала собери данные инструментами (обычно 2-4 вызова: снимок прогноза, дневная история, рейтинги, лента моста), затем напиши на русском связный отчёт со структурой:',
  '1. Вводный абзац: дата и время снимка, текущая цена и изменение за 24 часа, итоговая оценка состояния рынка одним предложением с выделенным выводом (жирным **...**).',
  '2. «Что произошло»: объёмы покупок и продаж WGNK/USDT за 24 часа и 7 дней, перевес покупок, движение цены.',
  '3. «Покупатели»: крупнейшие покупатели из рейтинга с точными суммами WGNK и USDT, упомяни группы вероятных одного участника (адреса перечисляй сокращённо, например 0x1234…abcd), порох покупателей (стейблкоины собственные и в цепочках) и степень покрытия балансов.',
  '4. «Продавцы и мост»: крупнейшие продавцы с их запасами WGNK/GNK, операции моста за 24 часа (минты против бёрнов) и что это значит для предложения.',
  '5. «Ликвидность»: глубина книги в диапазонах ±2% / ±5% / ±10% / ±20% на последний день и как она менялась.',
  '6. «Оценка»: 3-5 пунктов выводов с оговорками о данных (концентрация, неполное покрытие пороха, связи по переводам — вероятность, не владелец).',
  'Стиль: плотный фактический, каждое утверждение с числом из полученных данных, никаких выдуманных чисел и адресов, никаких советов покупать или продавать, без дисклеймеров про не-финансовый-совет (это саммари данных). Объём 350-700 слов.'].join(' ');

 const llm={busy:false};
 function currentModel(){
  const select=el('forecast-llm-model');
  return select&&select.value?select.value:MODEL;
 }
 async function postModel(key,body){
  // "Failed to fetch" is a dropped connection before any response: worth retrying.
  let lastError=null;
  for(const wait of [0,3000,8000]){
   if(wait)await new Promise(done=>setTimeout(done,wait));
   try{return await fetch(OPENBROKER,{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
    body:JSON.stringify(body)});}
   catch(error){lastError=error;}
  }
  throw new Error('нет связи с api.openbroker.gonka.gg — проверьте интернет и повторите запрос');
 }
 async function callModel(key,messages,tools){
  const base={model:currentModel(),temperature:0,max_tokens:MAX_TOKENS,messages,
   tools:tools||undefined,tool_choice:tools?'auto':undefined};
  // Мышление high у всех агентов, провайдер один (OpenBroker): неизвестное
  // поле даёт 400 — пробуем известные формы параметра, затем обычный запрос.
  const attempts=[{...base,reasoning:{effort:'high'}},{...base,reasoning_effort:'high'},base];
  let lastError=null;
  for(const body of attempts){
   const response=await postModel(key,body);
   if(response.ok){
    let payload;const text=await response.text();
    try{payload=JSON.parse(text);}catch{throw new Error('модель ответила не JSON');}
    const choice=payload.choices?.[0];
    const content=(choice?.message?.content||'').trim();
    if(!content&&choice?.finish_reason==='length')
     throw new Error('модель исчерпала лимит вывода на размышление — повторите запрос');
    return {content,tool_calls:choice?.message?.tool_calls||null};
   }
   let detail='HTTP '+response.status;let concurrent=false;
   try{const payload=await response.json();
    const message=payload.error?.message||String(payload.error||payload.detail||'');
    detail+=': '+message;concurrent=/concurrent/i.test(message);}catch{}
   if(response.status===400){lastError=new Error(detail);continue;}
   if(response.status===429&&!concurrent)detail+=' · лимит запросов ключа OpenBroker: подождите минуту и повторите, либо проверьте квоту/тариф ключа';
   if(response.status===401)detail='Ключ не принят (401): проверьте, что скопировали ключ OpenBroker целиком';
   const error=new Error(detail);error.concurrent=concurrent;error.status=response.status;throw error;
  }
  throw lastError||new Error('модель не ответила');
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
    {role:'user',content:'Составь саммари рынка WGNK на сейчас. Вердикт правил сайта: '+
      (data.verdict?.label||'?')+' ('+(data.verdict?.score??0).toFixed(3)+'). Собери данные инструментами и напиши отчёт по заданной структуре.'}];
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
    const f=await getJSON('/api/mints/forecast');
    const flows=await getJSON('/api/mints/flows?side=all&limit=1');
    const rich=JSON.stringify({verdict:f.verdict,rules:(f.rules||[]).map(r=>[r.name,r.score,r.explanation]),
     powder:f.powder&&{totals:f.powder.totals,coverage:f.powder.balance_coverage,
      buyers:(f.powder.buyers||[]).slice(0,10),sellers:(f.powder.sellers||[]).slice(0,10)},
     daily_30d:(flows.daily||[]).slice(-30).map(d=>[d.date,+(Number(d.price_raw)/1e12).toFixed(6),+(Number(d.bought_raw)/1e9).toFixed(0),+(Number(d.sold_raw)/1e9).toFixed(0)])}).slice(0,50000);
    content=(await callModel(key,[{role:'system',content:SYSTEM_PROMPT},{role:'user',content:rich}],null)).content;
    toolNotes.push('без инструментов');
   }
   el('forecast-llm-reply').hidden=false;
   el('forecast-llm-reply').innerHTML=formatSummary(content||'(пустой ответ)');
   status.textContent='Готово · прямой вызов из браузера · модель '+currentModel()+
    (toolNotes.length?' · данные: '+toolNotes.join(', '):'');
  }finally{llm.busy=false;el('forecast-llm-ask').disabled=false;}
 }
 function formatSummary(text){
  // Minimal safe markdown: bold and paragraphs; everything else stays literal.
  const html=esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  const parts=html.split('\n\n');
  return parts.map(p=>'<p>'+p.split('\n').join('<br>')+'</p>').join('');
 }
 el('forecast-llm-ask').addEventListener('click',()=>askModel().catch(error=>{el('forecast-llm-status').textContent='Ошибка: '+error.message;}));
 el('forecast-llm-key').addEventListener('keydown',event=>{if(event.key==='Enter')el('forecast-llm-ask').click();});
 try{el('forecast-llm-key').value=localStorage.getItem(KEY_STORAGE)||'';}catch{}
 try{el('forecast-llm-model').value=localStorage.getItem(MODEL_STORAGE)||MODEL;}catch{}
 el('forecast-llm-model').addEventListener('change',event=>{
  try{localStorage.setItem(MODEL_STORAGE,event.target.value);}catch{}});
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
