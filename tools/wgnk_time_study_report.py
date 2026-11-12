"""Render local, static research deliverables from the frozen analysis."""
import json
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import wgnk_time_study as s

OUT=s.OUT
x=json.loads((OUT/'analysis.json').read_text(encoding='utf-8'))
source=json.loads((OUT/'snapshot.json').read_text(encoding='utf-8'))
pool=x['pools'][x['main_pool']]
days=pool['days']
names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']


def number(value,decimals=0):
    return f'{float(value):,.{decimals}f}'


def link_time(d,kind):
    if d[kind+'_carried']:
        return '00:00:00 *'
    stamp=datetime.fromisoformat(d[kind+'_time']).strftime('%H:%M:%S')
    return f'[{stamp}](https://etherscan.io/tx/{d[kind+"_tx"]})'


lines=['# WGNK daily lows and highs — MSK','',
 '10 June – 5 September 2026, 88 full days. Main Uniswap V3 WGNK/USDT pool, 0.3% fee.',
 '', 'Price is USDT per 1 WGNK, the pool state after the last trade of a block. Intra-block spikes are excluded. The extreme time is when it was first reached during the day. The price may stay unchanged for several hours without trades.',
 '', '`*` — the extreme was already present in the 00:00 price carried over from the previous day; it is not a midnight transaction. Time links lead to Ethereum transactions.',
 '', '| Date | Day | Low, USDT | Low time, MSK | High, USDT | High time, MSK | Swap events |',
 '|---|---|---:|---|---:|---|---:|']
for d in days:
    lines.append(f'| {datetime.fromisoformat(d["date"]).strftime("%d/%m/%Y")} | {names[d["weekday"]]} | {d["low"]:.6f} | {link_time(d,"low")} | {d["high"]:.6f} | {link_time(d,"high")} | {d["events"]} |')
lines.extend(['','## Method and reproducibility','',
 'Ethereum snapshot #25,919,155, 6 September 2026 18:18:47 MSK. The unfinished day of 6 September is not included. WGNK contract: `0x972a7a92d92796a98801a8818bcf91f1648f2f68`.',
 '', 'Prices are restored from `sqrtPriceX96` of all Swap events matched with the local verified archive by block hash, pool, transaction hash, logIndex, direction and integer amounts. Formula: `sqrtPriceX96² × 1000 / 2¹⁹²`. Between events the unchanged pool state is carried forward; no trades are invented.',
 '', 'Pool: https://etherscan.io/address/'+x['main_pool'],
 '', 'Swap event description: https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolEvents.sol'])
(OUT/'DAILY_EXTREMES.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

regular=[r for r in x['sellers'] if r['days']>=10]
lines=['# WGNK sellers and activity time','',
 'Period: 5 June – 5 September 2026, both tracked pools. Only sales with a confirmed net WGNK outflow of the initiator. Several Swap events of one transaction of one address are merged. One address is not necessarily one person.',
 '', '“Sale days” is the number of calendar days with at least one confirmed sale. The share in a window is counted from those days, not from all days of the period. Windows were chosen from data already seen; this is not a forecast. Addresses with sales on at least 10 different days were selected.',
 '', '| Address | WGNK sold | Sale days | Frequent hour MSK | Days in that hour | Frequent 3-hour window | Days in window |',
 '|---|---:|---:|---|---:|---|---:|']
for r in regular:
    a=r['address'];h=r['best_hour'];w=r['best_3h']
    lines.append(f'| [{a}](https://etherscan.io/address/{a}) | {number(Fraction(int(r["sold_raw"]),10**9))} | {r["days"]} | {h:02}:00–{h+1:02}:00 | {r["best_hour_days"]}/{r["days"]} | {w:02}:00–{w+3:02}:00 | {r["best_3h_days"]}/{r["days"]} |')
lines.extend(['','## Verified links and examples','',
 'All 13 regularly selling addresses have a WGNK mint and a verified link to a Gonka address. This proves use of the bridge, not that the sold tokens were mined. None of them has a current host label in the local snapshot. The label list is incomplete and is not a historical registry of all miners.',
 '', 'The studied sample has one other seller linked to an address labelled as a host: `0x8cbb74a73c2f8eb867a1b1990cc10186748dbfcd` → `gonka10mmdjau4dnj8krs7sh7t7635ttnmq9u3vqgz09`. It has only one sale day, 30 June 2026: a schedule cannot be judged from it. The host label refers to the epoch 383 snapshot and does not prove the status on the day of the June sale.'])
for prefix in ['0x4053','0x4ea0','0xe3a3']:
    r=next(r for r in regular if r['address'].startswith(prefix))
    lines.extend(['','### '+r['address'],'',
        'Linked Gonka address: '+', '.join('`'+a+'`' for a in r['native_sources'])+'.',
        '', f'Sales of this address directly set the daily low of the main pool on {r["low_days"]} of 88 full days. This is a match on the last trade of a block, not a claim that the address alone caused the whole daily drop.',
        '', '| Date and time MSK | WGNK sold | Transaction |','|---|---:|---|'])
    for e in r['window_examples']:
        lines.append(f'| {datetime.fromisoformat(e["time"]).strftime("%d/%m/%Y %H:%M:%S")} | {number(e["qty"])} | [Ethereum ↗](https://etherscan.io/tx/{e["tx"]}) |')
    ev=[e for e in source['trades'] if e['actor']==r['address'] and e['kind']=='sell' and e['meta'].get('attribution')=='initiator_net' and e['ts']<datetime(2026,9,6,tzinfo=s.MSK).timestamp()]
    lines.extend(['','| Month | Sale days | Days with a sale 00–03 | Days with a sale 06–09 | Days with a sale 15–18 |', '|---|---:|---:|---:|---:|'])
    for month in ['2026-06','2026-07','2026-08','2026-09']:
        stamps=[datetime.fromtimestamp(e['ts'],s.MSK) for e in ev if datetime.fromtimestamp(e['ts'],s.MSK).isoformat().startswith(month)]
        counts=[len({dt.date() for dt in stamps if h<=dt.hour<h+3}) for h in [0,6,15]]
        lines.append(f'| {month} | {len({dt.date() for dt in stamps})} | {counts[0]} | {counts[1]} | {counts[2]} |')
(OUT/'SELLERS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

# A small static scientific plot; no external rendering or AI-generated artwork.
im=Image.new('RGB',(1600,780),'#f5f7f9');draw=ImageDraw.Draw(im)
fonts=Path('C:/Windows/Fonts')
def font(size,bold=False):return ImageFont.truetype(str(fonts/('arialbd.ttf' if bold else 'arial.ttf')),size)
ink='#18303b';muted='#586c75';wd='#16836b';we='#6756bb'
draw.text((62,35),'When WGNK daily extremes occurred',font=font(38,True),fill=ink)
draw.text((64,91),'10 June — 5 September 2026  ·  MSK  ·  main WGNK / USDT 0.3% pool',font=font(22),fill=muted)
draw.rounded_rectangle((63,137,83,153),radius=4,fill=wd);draw.text((96,133),'Weekdays — 63 days',font=font(20),fill=ink)
draw.rounded_rectangle((330,137,350,153),radius=4,fill=we);draw.text((363,133),'Weekends — 25 days',font=font(20),fill=ink)
for left,kind,title in [(80,'low','DAILY LOWS'),(850,'high','DAILY HIGHS')]:
    top=260;bottom=578;width=655
    draw.text((left,197),title,font=font(23,True),fill=ink)
    draw.text((left,229),'Share of calendar days, %',font=font(16),fill=muted)
    for tick in range(0,51,10):
        y=bottom-(bottom-top)*tick/50
        draw.line((left,y,left+width,y),fill='#dfe5e8',width=1)
        draw.text((left-35,y-9),str(tick),font=font(16),fill=muted)
    for i,h in enumerate(range(0,24,3)):
        center=left+(i+.5)*width/8
        for dx,group,color in [(-23,'weekday',wd),(3,'weekend',we)]:
            g=pool[group];n=g['actual_new_'+kind+'_3h'][str(h)];pct=n/g['calendar_days']*100;y=bottom-(bottom-top)*pct/50
            if n:
                draw.rounded_rectangle((center+dx,y,center+dx+20,bottom),radius=3,fill=color)
                text=str(n);box=draw.textbbox((0,0),text,font=font(16,True))
                draw.text((center+dx+10-(box[2]-box[0])/2,y-24),text,font=font(16,True),fill=color)
        label=f'{h:02}–{h+3:02}'
        box=draw.textbbox((0,0),label,font=font(16))
        draw.text((center-(box[2]-box[0])/2,bottom+15),label,font=font(16),fill=ink)
    draw.text((left,622),'MSK hours · numbers above the bars are day counts',font=font(16),fill=muted)
draw.line((64,671,1535,671),fill='#d6dfe4',width=1)
draw.text((64,691),'Price after the last Swap in a block. Extremes carried over from midnight are not attributed to new trades:',font=font(18),fill=muted)
draw.text((64,720),'6 days for the low and 13 for the high. Historical frequencies do not imply a stable schedule or a forecast.',font=font(18),fill=muted)
im.save(OUT/'hourly-extremes.png')
print(json.dumps({'files':['DAILY_EXTREMES.md','SELLERS.md','hourly-extremes.png'],'days':len(days),'regular_sellers':len(regular)},ensure_ascii=False))
