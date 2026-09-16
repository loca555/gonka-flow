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
names=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']


def number(value,decimals=0):
    return f'{float(value):,.{decimals}f}'.replace(',',' ')


def link_time(d,kind):
    if d[kind+'_carried']:
        return '00:00:00 *'
    stamp=datetime.fromisoformat(d[kind+'_time']).strftime('%H:%M:%S')
    return f'[{stamp}](https://etherscan.io/tx/{d[kind+"_tx"]})'


lines=['# Дневные минимумы и максимумы WGNK — МСК','',
 '10.06–05.09.2026, 88 полных дней. Основной пул Uniswap V3 WGNK/USDT, комиссия 0.3%.',
 '', 'Цена — USDT за 1 WGNK, состояние пула после последней сделки блока. Внутриблочные всплески исключены. Время экстремума — его первое достижение за сутки. Цена может сохраняться несколько часов без сделок.',
 '', '`*` — экстремум уже присутствовал в цене на 00:00, перенесённой с предыдущих суток; это не транзакция в полночь. Ссылки времени ведут на транзакции Ethereum.',
 '', '| Дата | День | Лой, USDT | Время лоя, МСК | Хай, USDT | Время хая, МСК | Swap-событий |',
 '|---|---|---:|---|---:|---|---:|']
for d in days:
    lines.append(f'| {datetime.fromisoformat(d["date"]).strftime("%d.%m.%Y")} | {names[d["weekday"]]} | {d["low"]:.6f} | {link_time(d,"low")} | {d["high"]:.6f} | {link_time(d,"high")} | {d["events"]} |')
lines.extend(['','## Методика и воспроизводимость','',
 'Снимок Ethereum #25 919 155, 06.09.2026 18:18:47 МСК. Незаконченный день 6 сентября не включён. Контракт WGNK: `0x972a7a92d92796a98801a8818bcf91f1648f2f68`.',
 '', 'Цены восстановлены из `sqrtPriceX96` всех Swap-событий, сопоставленных с локальным проверенным архивом по хешу блока, пулу, хешу транзакции, logIndex, направлению и целочисленным суммам. Формула: `sqrtPriceX96² × 1000 / 2¹⁹²`. Между событиями перенесено неизменное состояние пула, а не выдуманы сделки.',
 '', 'Пул: https://etherscan.io/address/'+x['main_pool'],
 '', 'Описание события Swap: https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolEvents.sol'])
(OUT/'DAILY_EXTREMES.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

regular=[r for r in x['sellers'] if r['days']>=10]
lines=['# Продавцы WGNK и время активности','',
 'Период: 05.06–05.09.2026, оба отслеживаемых пула. Только продажи с подтверждённым чистым оттоком WGNK у инициатора. Несколько Swap-событий одной транзакции одного адреса объединены. Один адрес — не обязательно один человек.',
 '', '«Дней продаж» — число календарных дней с хотя бы одной подтверждённой продажей. Доля в окне считается от этих дней, не от всех дней периода. Окна выбраны по уже увиденным данным, это не прогноз. Отобраны адреса с продажами минимум в 10 разных дней.',
 '', '| Адрес | Продано WGNK | Дней продаж | Частый час МСК | Дней в этот час | Частое 3-часовое окно | Дней в окне |',
 '|---|---:|---:|---|---:|---|---:|']
for r in regular:
    a=r['address'];h=r['best_hour'];w=r['best_3h']
    lines.append(f'| [{a}](https://etherscan.io/address/{a}) | {number(Fraction(int(r["sold_raw"]),10**9))} | {r["days"]} | {h:02}:00–{h+1:02}:00 | {r["best_hour_days"]}/{r["days"]} | {w:02}:00–{w+3:02}:00 | {r["best_3h_days"]}/{r["days"]} |')
lines.extend(['','## Проверенные связи и примеры','',
 'У всех 13 регулярно продающих адресов обнаружена чеканка WGNK и проверенная связь с Gonka-адресом. Это доказывает использование моста, но не добычу проданных токенов. Среди них текущая метка хоста в локальном снимке не найдена. Список меток неполон и не является историческим реестром всех майнеров.',
 '', 'В исследованной выборке есть один другой продавец со связью с адресом, помеченным хостом: `0x8cbb74a73c2f8eb867a1b1990cc10186748dbfcd` → `gonka10mmdjau4dnj8krs7sh7t7635ttnmq9u3vqgz09`. У него лишь один день продаж, 30.06.2026: оценивать расписание по нему нельзя. Метка хоста относится к снимку эпохи 383, а не доказывает статус на день июньской продажи.'])
for prefix in ['0x4053','0x4ea0','0xe3a3']:
    r=next(r for r in regular if r['address'].startswith(prefix))
    lines.extend(['','### '+r['address'],'',
        'Связанный адрес Gonka: '+', '.join('`'+a+'`' for a in r['native_sources'])+'.',
        '', f'Продажи этого адреса непосредственно установили дневной минимум основного пула в {r["low_days"]} из 88 полных дней. Это совпадение по последней сделке блока, не утверждение, что адрес единолично вызвал всё дневное падение.',
        '', '| Дата и время МСК | Продано WGNK | Транзакция |','|---|---:|---|'])
    for e in r['window_examples']:
        lines.append(f'| {datetime.fromisoformat(e["time"]).strftime("%d.%m.%Y %H:%M:%S")} | {number(e["qty"])} | [Ethereum ↗](https://etherscan.io/tx/{e["tx"]}) |')
    ev=[e for e in source['trades'] if e['actor']==r['address'] and e['kind']=='sell' and e['meta'].get('attribution')=='initiator_net' and e['ts']<datetime(2026,9,6,tzinfo=s.MSK).timestamp()]
    lines.extend(['','| Месяц | Дней продаж | Дней с продажей 00–03 | Дней с продажей 06–09 | Дней с продажей 15–18 |', '|---|---:|---:|---:|---:|'])
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
draw.text((62,35),'Когда возникали дневные экстремумы WGNK',font=font(38,True),fill=ink)
draw.text((64,91),'10 июня — 5 сентября 2026  ·  МСК  ·  основной пул WGNK / USDT 0.3%',font=font(22),fill=muted)
draw.rounded_rectangle((63,137,83,153),radius=4,fill=wd);draw.text((96,133),'Будни — 63 дня',font=font(20),fill=ink)
draw.rounded_rectangle((330,137,350,153),radius=4,fill=we);draw.text((363,133),'Выходные — 25 дней',font=font(20),fill=ink)
for left,kind,title in [(80,'low','ДНЕВНЫЕ МИНИМУМЫ'),(850,'high','ДНЕВНЫЕ МАКСИМУМЫ')]:
    top=260;bottom=578;width=655
    draw.text((left,197),title,font=font(23,True),fill=ink)
    draw.text((left,229),'Доля календарных дней, %',font=font(16),fill=muted)
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
    draw.text((left,622),'Часы МСК · числа над столбиками — количество дней',font=font(16),fill=muted)
draw.line((64,671,1535,671),fill='#d6dfe4',width=1)
draw.text((64,691),'Цена после последнего Swap в блоке. Экстремумы, сохранённые с полуночи, не приписаны новым сделкам:',font=font(18),fill=muted)
draw.text((64,720),'6 дней для лоя и 13 для хая. Исторические частоты не означают устойчивое расписание или прогноз.',font=font(18),fill=muted)
im.save(OUT/'hourly-extremes.png')
print(json.dumps({'files':['DAILY_EXTREMES.md','SELLERS.md','hourly-extremes.png'],'days':len(days),'regular_sellers':len(regular)},ensure_ascii=False))
