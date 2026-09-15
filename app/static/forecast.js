/* Forecast: deterministic rules over verified data plus an optional external model.
   The API key never leaves the browser except directly to the model provider. */
(()=>{
 const state={data:null,loading:false,request:0,abort:null};
 const KEY_STORAGE='gonka-forecast-llm-key';
 const OPENBROKER='https://api.openbroker.gonka.gg/v1/chat/completions';
 const MODEL='deepseek-ai/DeepSeek-V4-Flash-0731';
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
 async function askModel(){
  const key=el('forecast-llm-key').value.trim();
  const status=el('forecast-llm-status');
  if(!key){status.textContent='Введите ключ API.';return;}
  try{localStorage.setItem(KEY_STORAGE,key);}catch{}
  status.textContent='Спрашиваем модель…';
  const data=state.data;
  if(!data?.ready){status.textContent='Снимок прогноза ещё не готов.';return;}
  const body=JSON.stringify({model:MODEL,temperature:0,messages:[{role:'user',content:buildPrompt(data)}]});
  let reply=null,mode='';
  try{
   const response=await fetch(OPENBROKER,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body});
   if(!response.ok)throw new Error('HTTP '+response.status);
   reply=(await response.json()).choices?.[0]?.message?.content?.trim();mode='прямой вызов из браузера';
  }catch(directError){
   if(directError instanceof TypeError||/Failed to fetch|NetworkError/i.test(String(directError))){
    const response=await fetch('/api/mints/forecast/llm',{method:'POST',headers:{'Content-Type':'application/json','x-llm-key':key}});
    if(!response.ok)throw new Error((await response.json()).detail||('HTTP '+response.status));
    reply=(await response.json()).reply;mode='транзит через сервер без сохранения ключа';
   }else throw directError;
  }
  el('forecast-llm-reply').textContent=reply||'(пустой ответ)';
  status.textContent='Готово · '+mode+' · модель '+MODEL;
 }
 function buildPrompt(data){
  const rules=(data.rules||[]).map(r=>r.name+'='+r.score.toFixed(2)).join('; ');
  const t=data.powder?.totals||{};
  return 'Ты аналитик рынка токена WGNK (Gonka). По проверенным данным ответь одной строкой: ВЕРДИКТ: рост или падение или нейтрально — и одно предложение объяснения на русском.\n'+
   'Цена сейчас: '+(data.price_raw?(Number(data.price_raw)/1e12).toFixed(6):'?')+' USDT. Правила: '+rules+'. Итог правил: '+
   (data.verdict?.label||'?')+'. Порох покупателей USDT: '+(Number(t.buy_own_raw||0)/1e6).toFixed(0)+' + цепочки '+(Number(t.buy_chain_raw||0)/1e6).toFixed(0)+
   '. Запасы продавцов WGNK: '+(Number(t.sell_wgnk_raw||0)/1e9).toFixed(0)+', GNK: '+(Number(t.sell_gnk_raw||0)/1e9).toFixed(0)+', эскроу GNK: '+(Number(t.escrow_raw||0)/1e9).toFixed(0)+'.';
 }
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
 el('forecast-llm-ask').addEventListener('click',()=>askModel().catch(error=>{el('forecast-llm-status').textContent='Ошибка: '+error.message;}));
 el('forecast-llm-key').addEventListener('keydown',event=>{if(event.key==='Enter')el('forecast-llm-ask').click();});
 try{el('forecast-llm-key').value=localStorage.getItem(KEY_STORAGE)||'';}catch{}
 window.addEventListener('gonka:view',()=>{if(!el('forecast-view').hidden)refresh();});
 setInterval(()=>{if(!document.hidden&&!el('forecast-view').hidden)refresh();},60000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!el('forecast-view').hidden)refresh();});
 if(!el('forecast-view').hidden)refresh();
})();
