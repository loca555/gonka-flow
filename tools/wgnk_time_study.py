"""Read-only, reproducible WGNK intraday research. Never edits the live database."""
import argparse
import hashlib
import json
import math
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test-results" / "wgnk-intraday-2026-09-06"
DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
MSK = timezone(timedelta(hours=3))
SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
RPCS = ["https://rpc.mevblocker.io", "https://ethereum.public.blockpi.network/v1/rpc/public"]

EXPORT = r'''
import sqlite3,json,time
c=sqlite3.connect('file:/data/gonka-flow.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
c.execute('BEGIN')
def kv(key):
 r=c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
 return json.loads(r[0]) if r else None
s=kv('flow:snapshot');d=kv('mints:deployment');start=d['height'];cut=s['height'];end=start-1
for r in c.execute("SELECT lo,hi FROM ranges WHERE chain='ethereum' ORDER BY lo"):
 if r['hi']<start:continue
 if r['lo']>end+1:break
 end=min(cut,max(end,r['hi']))
 if end>=cut:break
assert end==cut and s['ledger_verified'], 'Need contiguous verified snapshot'
rows=[dict(r) for r in c.execute("SELECT * FROM events WHERE chain='ethereum' AND finalized=1 AND height BETWEEN ? AND ? AND kind IN ('buy','sell','bridge_mint') ORDER BY height,idx,id",(start,cut))]
for r in rows:r['meta']=json.loads(r['meta'])
result={'extracted_at':int(time.time()),'snapshot':s,'deployment':d,'range_verified':[start,end],
 'trades':[r for r in rows if r['kind'] in ('buy','sell')],
 'mints':[r for r in rows if r['kind']=='bridge_mint'],
 'mint_links':[json.loads(r[0]) for r in c.execute('SELECT value FROM gonka_mint_links') if json.loads(r[0])['eth_height']<=cut],
 'labels':[dict(r) for r in c.execute('SELECT * FROM labels')],
 'flow_status':kv('flow:status')}
c.rollback();c.close();print(json.dumps(result,separators=(',',':')))
'''


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rpc(method, params):
    errors = []
    for url in RPCS:
        try:
            request = urllib.request.Request(url, json.dumps({"jsonrpc":"2.0","id":1,
                "method":method,"params":params}).encode(), {"Content-Type":"application/json", "User-Agent":"WGNK-intraday-research/1.0"})
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.load(response)
            if data.get("error"):
                raise ValueError(str(data["error"]))
            return data["result"], url
        except Exception as error:
            errors.append(url + ": " + str(error)[:180])
    raise RuntimeError("; ".join(errors))


def collect():
    OUT.mkdir(parents=True, exist_ok=True)
    file = OUT / "snapshot.json"
    if not file.exists():
        data = json.loads(subprocess.check_output([DOCKER,"exec","gonka-live-gonka-flow-1",
                                                   "python","-c",EXPORT], text=True))
        dump("snapshot.json", data)
    else:
        data = json.loads(file.read_text(encoding="utf-8"))
    trades = data["trades"]
    print(json.dumps({"trades":len(trades), "first":datetime.fromtimestamp(trades[0]["ts"],MSK).isoformat(),
                      "snapshot":datetime.fromtimestamp(data["snapshot"]["ts"],MSK).isoformat()}), flush=True)
    cache_file = OUT / "swap_prices.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {"prices":{},"ranges":[]}
    if len(cache["prices"]) == len(trades):
        return data, cache
    chain, provider = rpc("eth_chainId", [])
    if int(chain,16) != 1:
        raise ValueError("Wrong Ethereum chain")
    expected = {e["id"]:e for e in trades}
    first, last = trades[0]["height"], data["snapshot"]["height"]
    for lo in range(first, last+1, 10000):
        hi = min(last, lo+9999)
        if [lo,hi] in cache["ranges"]:
            continue
        logs, provider = rpc("eth_getLogs", [{"address":[p["address"] for p in data["snapshot"]["pools"]],
                    "topics":[SWAP],"fromBlock":hex(lo),"toBlock":hex(hi)}])
        seen = set()
        for log in logs:
            ident = "ethereum:" + log["transactionHash"].lower() + ":" + str(int(log["logIndex"],16))
            row = expected.get(ident)
            if row is None or log.get("removed"):
                raise ValueError("RPC swap not in verified local snapshot")
            if log["blockHash"].lower()!=row["block_hash"] or log["address"].lower()!=row["pool"]:
                raise ValueError("RPC block/pool mismatch")
            words = [int(log["data"][2+i:2+i+64],16) for i in range(0,320,64)]
            a0,a1 = (v-2**256 if v>=2**255 else v for v in words[:2])
            if abs(a0)!=int(row["amount_raw"]) or abs(a1)!=int(row["quote_raw"]):
                raise ValueError("RPC swap amount mismatch")
            if (a0>0)!=(row["kind"]=="sell"):
                raise ValueError("RPC swap direction mismatch")
            if words[2]<=0:
                raise ValueError("Invalid sqrt price")
            cache["prices"][ident] = {"sqrt_price_x96":str(words[2]),"liquidity":str(words[3]),
                "tick":words[4]-2**256 if words[4]>=2**255 else words[4],"source":provider}
            seen.add(ident)
        need = {e["id"] for e in trades if lo<=e["height"]<=hi}
        if seen!=need:
            raise ValueError("RPC omitted swaps: " + str(len(need-seen)))
        cache["ranges"].append([lo,hi]);dump("swap_prices.json",cache)
        print(json.dumps({"verified_prices":len(cache["prices"]),"total":len(trades),"through":hi}),flush=True)
        time.sleep(.7)
    assert len(cache["prices"])==len(trades)
    return data, cache


def iso(ts):
    return datetime.fromtimestamp(ts,MSK).isoformat()


def rounded(value, digits=6):
    return round(float(value),digits)


def histogram(rows, field, width=1):
    counts = Counter(int(r[field])//width*width for r in rows if r[field] is not None)
    return {str(h):counts[h] for h in range(0,24,width)}


def make_days(trades, last_midnight):
    """Constant pool state between swaps, keeping only each block's final swap state."""
    by_block = {e['height']:e for e in sorted(trades,key=lambda e:(e['height'],e['idx']))}
    changes = sorted(by_block.values(),key=lambda e:(e['height'],e['idx']))
    first = datetime.fromtimestamp(changes[0]['ts'],MSK).replace(hour=0,minute=0,second=0,microsecond=0)
    start = int(first.timestamp())+86400  # Exclude launch's incomplete calendar day.
    result, pos, prior = [], 0, None
    while start<last_midnight:
        while pos<len(changes) and changes[pos]['ts']<start:
            prior=changes[pos];pos+=1
        end=start+86400
        daily=[]
        while pos<len(changes) and changes[pos]['ts']<end:
            daily.append(changes[pos]);pos+=1
        if prior is None:
            start=end;continue
        observations=[{**prior,'ts':start,'carried':True}]+[{**e,'carried':False} for e in daily]
        lo=min(observations,key=lambda e:e['numerator']);hi=max(observations,key=lambda e:e['numerator'])
        fresh_lo=min(daily,key=lambda e:e['numerator']) if daily else None
        fresh_hi=max(daily,key=lambda e:e['numerator']) if daily else None
        sums=[0]*24;near_lo=[0]*24;near_hi=[0]*24;bottom=[0]*24
        for i,e in enumerate(observations):
            a=e['ts'];b=observations[i+1]['ts'] if i+1<len(observations) else end
            while a<b:
                h=(a-start)//3600;stop=min(b,start+(h+1)*3600);seconds=stop-a
                sums[h]+=e['numerator']*seconds
                if e['numerator']*200<=lo['numerator']*201:near_lo[h]+=seconds
                if e['numerator']*200>=hi['numerator']*199:near_hi[h]+=seconds
                if (e['numerator']-lo['numerator'])*5<=hi['numerator']-lo['numerator']:bottom[h]+=seconds
                a=stop
        raw_daily=[e for e in trades if start<=e['ts']<end]
        volumes={side:sum(int(e['amount_raw']) for e in raw_daily if e['kind']==side) for side in ('buy','sell')}
        quotes={side:sum(int(e['quote_raw']) for e in raw_daily if e['kind']==side) for side in ('buy','sell')}
        day=datetime.fromtimestamp(start,MSK)
        row={'date':day.date().isoformat(),'weekday':day.weekday(),'week':(day.date()-timedelta(days=day.weekday())).isoformat(),
          'events':len(raw_daily),'transactions':len({e['tx_hash'] for e in raw_daily}),
          'active_hours':len({e['hour'] for e in raw_daily}),
          'active_hour_list':sorted({e['hour'] for e in raw_daily}),
          'open':rounded(Fraction(observations[0]['numerator'],2**192)),
          'close':rounded(Fraction(observations[-1]['numerator'],2**192)),
          'low':rounded(Fraction(lo['numerator'],2**192)), 'high':rounded(Fraction(hi['numerator'],2**192)),
          'low_time':iso(lo['ts']),'high_time':iso(hi['ts']),
          'low_hour':datetime.fromtimestamp(lo['ts'],MSK).hour,'high_hour':datetime.fromtimestamp(hi['ts'],MSK).hour,
          'low_carried':lo['carried'],'high_carried':hi['carried'],
          'low_tx':lo['tx_hash'],'high_tx':hi['tx_hash'],
          'low_kind':lo['kind'],'high_kind':hi['kind'],
          'fresh_low_hour':fresh_lo['hour'] if fresh_lo else None,'fresh_high_hour':fresh_hi['hour'] if fresh_hi else None,
          'fresh_low_time':iso(fresh_lo['ts']) if fresh_lo else None,'fresh_high_time':iso(fresh_hi['ts']) if fresh_hi else None,
          'fresh_low_tx':fresh_lo['tx_hash'] if fresh_lo else None,'fresh_high_tx':fresh_hi['tx_hash'] if fresh_hi else None,
          'low_actor':lo['actor'] if not lo['carried'] and lo['kind']=='sell' and lo['meta'].get('attribution')=='initiator_net' else None,
          'range_pct':rounded(Fraction(hi['numerator']-lo['numerator'],lo['numerator'])*100),
          'return_pct':rounded(Fraction(observations[-1]['numerator']-observations[0]['numerator'],observations[0]['numerator'])*100),
          'buy_raw':str(volumes['buy']),'sell_raw':str(volumes['sell']),
          'buy_quote_raw':str(quotes['buy']),'sell_quote_raw':str(quotes['sell']),
          'twap':[float(Fraction(n,3600*2**192)) for n in sums],
          'near_low':[n/3600 for n in near_lo],'near_high':[n/3600 for n in near_hi],
          'bottom_fifth':[n/3600 for n in bottom]}
        result.append(row)
        if daily:prior=daily[-1]
        start=end
    return result


def profile(days, seed=5706):
    if len(days)<3:return None
    matrix=np.log(np.asarray([d['twap'] for d in days]))
    x=np.arange(24,dtype=float)+.5;x-=x.mean()
    residual=matrix-matrix.mean(axis=1,keepdims=True)-(matrix@x/(x@x))[:,None]*x
    mean=residual.mean(axis=0)
    rng=np.random.default_rng(seed);trials=5000;n=len(days)
    maximum=[];boot=[]
    for i in range(0,trials,100):
        shifts=rng.integers(0,24,size=(100,n,1))
        simulated=np.take_along_axis(np.broadcast_to(residual,(100,n,24)),(np.arange(24)[None,None,:]-shifts)%24,axis=2).mean(axis=1)
        maximum.extend(np.max(np.abs(simulated),axis=1).tolist())
        boot.extend(residual[rng.integers(0,n,size=(100,n))].mean(axis=1).tolist())
    max_obs=float(np.max(np.abs(mean)))
    p=(1+sum(v>=max_obs for v in maximum))/(trials+1)
    band=float(np.quantile(np.max(np.abs(np.array(boot)-mean),axis=1),.95))
    return {'days':n,'mean_detrended_pct':np.round(mean*100,4).tolist(),
            'min_hour':int(np.argmin(mean)),'max_hour':int(np.argmax(mean)),
            'min_pct':rounded(mean.min()*100,4),'max_pct':rounded(mean.max()*100,4),
            'simultaneous_bootstrap_band_pp':rounded(band*100,4),'clock_scan_p':rounded(p,5)}


def group_summary(days):
    trading=[d for d in days if d['events']]
    if not trading:return None
    eligible=[d for d in trading if d['events']>=10 and d['active_hours']>=8 and d['range_pct']>=1]
    hourly=[]
    for h in range(24):
        hourly.append({'hour':h,'fresh_lows':sum(d['fresh_low_hour']==h for d in trading),
          'fresh_highs':sum(d['fresh_high_hour']==h for d in trading),
          'active_days':sum(h in d['active_hour_list'] for d in trading),
          'near_low_pct':rounded(np.mean([d['near_low'][h] for d in eligible])*100,2) if eligible else None,
          'near_high_pct':rounded(np.mean([d['near_high'][h] for d in eligible])*100,2) if eligible else None})
    return {'calendar_days':len(days),'trading_days':len(trading),'eligible_days':len(eligible),
      'median_events':rounded(np.median([d['events'] for d in days]),1),
      'median_active_hours':rounded(np.median([d['active_hours'] for d in days]),1),
      'median_range_pct':rounded(np.median([d['range_pct'] for d in days]),3),
      'mean_range_pct':rounded(np.mean([d['range_pct'] for d in days]),3),
      'median_return_pct':rounded(np.median([d['return_pct'] for d in days]),3),
      'mean_quote_per_day':rounded(Fraction(sum(int(d['buy_quote_raw'])+int(d['sell_quote_raw']) for d in days),len(days)*10**6),2),
      'median_quote_per_day':rounded(np.median([(int(d['buy_quote_raw'])+int(d['sell_quote_raw']))/10**6 for d in days]),2),
      'sell_share_pct':rounded(Fraction(sum(int(d['sell_raw']) for d in days),sum(int(d['sell_raw'])+int(d['buy_raw']) for d in days))*100,2),
      'low_3h':histogram(trading,'fresh_low_hour',3),'high_3h':histogram(trading,'fresh_high_hour',3),
      'actual_new_low_3h':histogram([d for d in trading if not d['low_carried']],'low_hour',3),
      'actual_new_high_3h':histogram([d for d in trading if not d['high_carried']],'high_hour',3),
      'actual_new_low_1h':histogram([d for d in trading if not d['low_carried']],'low_hour'),
      'actual_new_high_1h':histogram([d for d in trading if not d['high_carried']],'high_hour'),
      'low_at_midnight_carried':sum(d['low_carried'] for d in trading),
      'high_at_midnight_carried':sum(d['high_carried'] for d in trading),
      'hourly':hourly,'profile':profile(eligible)}


def paired_weekends(days):
    weeks=defaultdict(list)
    for d in days:weeks[d['week']].append(d)
    pairs=[]
    for week,rows in weeks.items():
        if len(rows)!=7:continue
        wd=[d for d in rows if d['weekday']<5];we=[d for d in rows if d['weekday']>=5]
        def mean(rows,key):
            return np.mean([d[key] if key!='quote' else (int(d['buy_quote_raw'])+int(d['sell_quote_raw']))/10**6 for d in rows])
        pairs.append({'week':week,**{key:[float(mean(wd,key)),float(mean(we,key))] for key in ['quote','events','range_pct','return_pct']}})
    rng=np.random.default_rng(5707);result={'weeks':len(pairs),'pairs':pairs}
    for key in ['quote','events','range_pct','return_pct']:
        arr=np.array([r[key] for r in pairs]);diff=arr[:,1]-arr[:,0]
        signs=rng.choice([-1,1],size=(10000,len(diff)))
        p=(1+np.sum(np.abs((diff*signs).mean(axis=1))>=abs(diff.mean())))/10001
        boot=arr[rng.integers(0,len(arr),size=(10000,len(arr)))].mean(axis=1)
        ratio=boot[:,1]/boot[:,0] if key!='return_pct' else boot[:,1]-boot[:,0]
        result[key]={'weekday':rounded(arr[:,0].mean(),4),'weekend':rounded(arr[:,1].mean(),4),
           'ratio_or_diff':rounded(arr[:,1].mean()/arr[:,0].mean() if key!='return_pct' else diff.mean(),4),
           'ci95':np.round(np.quantile(ratio,[.025,.975]),4).tolist(),'paired_p':rounded(p,5)}
    return result


def sellers(data, trades, days):
    minters={e['dst'] for e in data['mints']}
    native=defaultdict(set)
    for link in data['mint_links']:native[link['eth_address']].add(link['gnk_address'])
    labels={r['address']:r for r in data['labels'] if r['role']=='host'}
    txs={}
    for e in trades:
        if e['kind']!='sell' or e['meta'].get('attribution')!='initiator_net':continue
        key=(e['actor'],e['tx_hash'])
        row=txs.setdefault(key,{'actor':e['actor'],'tx':e['tx_hash'],'ts':e['ts'],'date':e['date'],'hour':e['hour'],'qty':0,'quote':0,'swaps':0})
        row['qty']+=int(e['amount_raw']);row['quote']+=int(e['quote_raw']);row['swaps']+=1
    by_actor=defaultdict(list)
    for t in txs.values():by_actor[t['actor']].append(t)
    result=[]
    for address,events in by_actor.items():
        active=sorted({e['date'] for e in events});hday=[len({e['date'] for e in events if e['hour']==h}) for h in range(24)]
        hvol=[sum(e['qty'] for e in events if e['hour']==h) for h in range(24)]
        windows=[len({e['date'] for e in events if h<=e['hour']<h+3}) for h in range(0,24,3)]
        h=int(np.argmax(hday));w=int(np.argmax(windows))*3
        host_sources=sorted(set(native[address])&set(labels))
        result.append({'address':address,'minter':address in minters,'native_sources':sorted(native[address]),'host_sources':host_sources,
          'days':len(active),'txs':len(events),'sold_raw':str(sum(e['qty'] for e in events)),
          'sold':rounded(Fraction(sum(e['qty'] for e in events),10**9),3),
          'quote':rounded(Fraction(sum(e['quote'] for e in events),10**6),2),
          'best_hour':h,'best_hour_days':hday[h],'best_hour_share':rounded(Fraction(hday[h],len(active))*100,2),
          'best_3h':w,'best_3h_days':max(windows),'best_3h_share':rounded(Fraction(max(windows),len(active))*100,2),
          'hour_days':hday,'hour_volume_raw':[str(v) for v in hvol],
          'low_days':sum(d['low_actor']==address for d in days),
          'weekend_days':len({e['date'] for e in events if datetime.fromisoformat(e['date']).weekday()>=5}),
          'first':active[0],'last':active[-1],
          'window_examples':[{'time':iso(e['ts']),'qty':rounded(Fraction(e['qty'],10**9),3),'quote':rounded(Fraction(e['quote'],10**6),2),'tx':e['tx']} for e in events if e['hour']==h][-8:]})
    result.sort(key=lambda r:int(r['sold_raw']),reverse=True)
    return result, list(txs.values())


def analyze(data,cache):
    cutoff=datetime.fromtimestamp(data['snapshot']['ts'],MSK).replace(hour=0,minute=0,second=0,microsecond=0)
    rows=[]
    for source in data['trades']:
        e=dict(source);stamp=datetime.fromtimestamp(e['ts'],MSK)
        e.update(date=stamp.date().isoformat(),hour=stamp.hour,weekday=stamp.weekday(),
                 numerator=int(cache['prices'][e['id']]['sqrt_price_x96'])**2*1000)
        rows.append(e)
    pools={p['address']:p for p in data['snapshot']['pools']}
    pool_report={}
    for address,pool in pools.items():
        trades=[e for e in rows if e['pool']==address];full=[e for e in trades if e['ts']<cutoff.timestamp()]
        daily=make_days(trades,int(cutoff.timestamp()))
        pool_report[address]={'fee':pool['fee'],'trades':len(full),
          'volume_raw':str(sum(int(e['amount_raw']) for e in full)),
          'quote_raw':str(sum(int(e['quote_raw']) for e in full)),
          'first':iso(trades[0]['ts']),'days':daily,
          'all':group_summary(daily),'weekday':group_summary([d for d in daily if d['weekday']<5]),
          'weekend':group_summary([d for d in daily if d['weekday']>=5]),
          'saturday':group_summary([d for d in daily if d['weekday']==5]),
          'sunday':group_summary([d for d in daily if d['weekday']==6])}
    main=max(pool_report,key=lambda p:int(pool_report[p]['quote_raw']))
    daily=pool_report[main]['days'];all_full=[e for e in rows if e['ts']<cutoff.timestamp()]
    selling,txs=sellers(data,all_full,daily)
    valid=[d for d in daily if d['events']>=10 and d['active_hours']>=8 and d['range_pct']>=1]
    split=len(valid)//2
    splits={'first_half':profile(valid[:split]),'second_half':profile(valid[split:]),
            'strict_12hours':profile([d for d in valid if d['active_hours']>=12]),
            'last_30days':group_summary(daily[-30:])}
    weekends=[d for d in valid if d['weekday']>=5]
    splits.update(weekend_first_half=profile(weekends[:len(weekends)//2]),
        weekend_second_half=profile(weekends[len(weekends)//2:]),
        weekend_without_june13=profile([d for d in weekends if d['date']!='2026-06-13']),
        weekend_without_top2_ranges=profile(sorted(weekends,key=lambda d:d['range_pct'])[:-2]))
    months={month:group_summary([d for d in daily if d['date'].startswith(month)]) for month in sorted({d['date'][:7] for d in daily})}
    hourly=[]
    for h in range(24):
        events=[e for e in all_full if e['hour']==h]
        hourly.append({'hour':h,'events':len(events),'buy_events':sum(e['kind']=='buy' for e in events),
          'sell_events':sum(e['kind']=='sell' for e in events),
          'buy_raw':str(sum(int(e['amount_raw']) for e in events if e['kind']=='buy')),
          'sell_raw':str(sum(int(e['amount_raw']) for e in events if e['kind']=='sell'))})
    result={'method':{'timezone':'Europe/Moscow (UTC+3)','price':'final swap state of each block, sqrtPriceX96^2 * 1000 / 2^192',
         'partial_launch_and_current_days_excluded':True,'eligible':'at least 10 swaps, 8 active hours and 1% daily range',
         'clock_test':'5000 independent per-day circular shifts, max absolute hourly detrended mean; 24-hour scan corrected',
         'warning':'Exploratory; changing liquidity, few weekends, known-at-snapshot host labels, no token-lot tracing'},
      'snapshot':data['snapshot'],'range_verified':data['range_verified'],'main_pool':main,'pools':pool_report,
      'splits':splits,'months':months,'paired_weekends':paired_weekends(daily),
      'sellers':selling,'hourly_turnover':hourly,
      'snapshot_sha256':hashlib.sha256((OUT/'snapshot.json').read_bytes()).hexdigest(),
      'prices_sha256':hashlib.sha256((OUT/'swap_prices.json').read_bytes()).hexdigest()}
    dump('analysis.json',result)
    summary={**{k:result[k] for k in ['main_pool','splits','months','paired_weekends']},
      'pools':{a:{k:v for k,v in p.items() if k not in ('days',)} for a,p in pool_report.items()},
      'regular_sellers':[s for s in sorted(selling,key=lambda s:(s['best_hour_share'],s['days']),reverse=True) if s['days']>=10][:12],
      'top_sellers':selling[:8]}
    dump('summary.json',summary)
    print(json.dumps({'done':True,'main_pool':main,'days':len(daily),'regular_sellers':len([s for s in selling if s['days']>=10]),
                      'outputs':str(OUT)},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    data, cache=collect()
    if '--analyze' in __import__('sys').argv:
        analyze(data,cache)
