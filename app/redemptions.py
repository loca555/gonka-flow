"""Targeted WGNK burns -> Gonka recipients, verified against native bridge state.

No full-chain scan and no inferred address conversion. A completed bridge record
is not a discovered native transaction hash or a proof of common ownership.
"""
import asyncio
import json
import re
import time

from .codec import BURN
from .config import TOKEN, ZERO
from .db import tokens

PATH = '/chain-api/productscience/inference/inference/bridge_transaction/ethereum/'


def initialize(db):
    db.conn.executescript('''
      CREATE TABLE IF NOT EXISTS gonka_burn_links(
        event_id TEXT PRIMARY KEY, gnk_address TEXT NOT NULL, value TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS gonka_burn_address ON gonka_burn_links(gnk_address);
      CREATE TABLE IF NOT EXISTS gonka_burn_attempts(
        event_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, retry_at INTEGER NOT NULL,
        error TEXT NOT NULL);
    ''')


def burn_index(burn):
    meta = burn['meta'] if isinstance(burn['meta'], dict) else json.loads(burn['meta'])
    match = re.fullmatch(r'ethereum:([1-9][0-9]*):(0|[1-9][0-9]*)', burn['request_key'])
    if (burn['chain'] != 'ethereum' or burn['kind'] != 'bridge_burn' or not burn['finalized']
            or meta.get('contract') != TOKEN or meta.get('topic') != BURN
            or not re.fullmatch(r'0x[0-9a-f]{40}', burn['src']) or burn['src'] == ZERO
            or burn['dst'] != ZERO or not re.fullmatch(r'[1-9][0-9]*', burn['amount_raw'])
            or int(burn['amount_raw']) >= 2**256 or not match or int(match[1]) != burn['height']
            or not re.fullmatch(r'0x[0-9a-f]{64}', burn['tx_hash'])
            or not re.fullmatch(r'0x[0-9a-f]{64}', burn['block_hash']) or burn['idx'] < 0):
        raise ValueError('Некорректное финальное событие сжигания WGNK')
    index = int(match[2])
    # Public starter archives retain request_key even when metadata was stripped.
    if 'transaction_index' in meta and meta['transaction_index'] != index:
        raise ValueError('Индекс транзакции не совпадает с координатами сжигания')
    return index


def verify_burn(burn, reply):
    from .provenance import GNK, SOURCE
    index = burn_index(burn)
    records = reply.get('bridgeTransactions')
    if not isinstance(records, list):
        raise ValueError('Неполный ответ о состоянии обратного моста')
    matches = [r for r in records if isinstance(r, dict)
               and r.get('chainId') == 'ethereum' and str(r.get('contractAddress', '')).lower() == TOKEN
               and str(r.get('blockNumber')) == str(burn['height'])
               and str(r.get('receiptIndex')) == str(index) and r.get('amount') == burn['amount_raw']]
    if len(matches) != 1:
        raise ValueError('Однозначная запись обратного моста ещё не найдена')
    record = matches[0]
    if record.get('status') != 'BRIDGE_COMPLETED':
        raise ValueError('Сжигание найдено; зачисление GNK ещё не подтверждено')
    if (not GNK.fullmatch(record.get('ownerAddress', ''))
            or not re.fullmatch(r'0|[1-9][0-9]*', record.get('epochIndex', ''))
            or not isinstance(record.get('id'), str) or not 0 < len(record['id']) <= 200):
        raise ValueError('Некорректный получатель или идентификатор обратного моста')
    return {'event_id':burn['id'], 'tx_hash':burn['tx_hash'], 'log_index':burn['idx'],
            'eth_address':burn['src'], 'eth_height':burn['height'], 'eth_ts':burn['ts'],
            'eth_block_hash':burn['block_hash'], 'receipt_index':index,
            'amount_raw':burn['amount_raw'], 'gnk_address':record['ownerAddress'],
            'request_id':record['id'], 'epoch_id':record['epochIndex'],
            'status':'completed', 'verification':'gonka_bridge_state',
            'evidence_url':SOURCE+PATH+str(burn['height'])+'/'+str(index),
            'verified_at':int(time.time()), 'gnk_tx_hash':None, 'gnk_ts':None}


def save_burn_link(db, link):
    burn = db.conn.execute('SELECT * FROM events WHERE id=?', (link['event_id'],)).fetchone()
    if not burn:
        raise ValueError('Нет исходного сжигания в архиве Ethereum')
    expected=verify_burn(dict(burn),{'bridgeTransactions':[{'chainId':'ethereum',
        'contractAddress':TOKEN,'ownerAddress':link['gnk_address'],'amount':link['amount_raw'],
        'blockNumber':str(link['eth_height']),'receiptIndex':str(link['receipt_index']),
        'status':'BRIDGE_COMPLETED','id':link['request_id'],'epochIndex':link['epoch_id']}]})
    if (set(link)!=set(expected) or any(link[k]!=v for k,v in expected.items() if k!='verified_at')
            or type(link['verified_at']) is not int or link['verified_at']<1):
        raise ValueError('Некорректная проверенная связь обратного моста')
    if any(link[a] != burn[b] for a,b in [('tx_hash','tx_hash'),('log_index','idx'),
            ('eth_address','src'),('eth_height','height'),('eth_ts','ts'),
            ('eth_block_hash','block_hash'),('amount_raw','amount_raw')]):
        raise ValueError('Связь не совпадает с исходным сжиганием')
    if db.conn.execute("SELECT COUNT(*) FROM events WHERE chain='ethereum' AND kind='bridge_burn' AND finalized=1 AND tx_hash=?", (burn['tx_hash'],)).fetchone()[0] != 1:
        raise ValueError('Несколько сжиганий в одной транзакции: автоматическое сопоставление неоднозначно')
    with db.conn:
        previous = db.conn.execute('SELECT value FROM gonka_burn_links WHERE event_id=?', (link['event_id'],)).fetchone()
        if previous:
            old = json.loads(previous[0])
            if any(old.get(k) != v for k,v in link.items() if k != 'verified_at'):
                raise ValueError('Противоречивая подтверждённая связь обратного моста')
            return
        db.conn.execute('INSERT INTO gonka_burn_links VALUES(?,?,?)',
                        (link['event_id'],link['gnk_address'],json.dumps(link)))
        db.conn.execute('DELETE FROM gonka_burn_attempts WHERE event_id=?',(link['event_id'],))
    db.revision += 1


def burn_overview(db, address=None, through=None):
    where, args = ["e.chain='ethereum'", "e.kind='bridge_burn'", 'e.finalized=1'], []
    if address:
        where.append('e.src=?');args.append(address)
    if through is not None:
        where.append('e.height<=?');args.append(through)
    rows = db.conn.execute('''SELECT e.*,l.value AS link_value,a.error,a.retry_at FROM events e
        LEFT JOIN gonka_burn_links l ON l.event_id=e.id
        LEFT JOIN gonka_burn_attempts a ON a.event_id=e.id WHERE '''+' AND '.join(where)+
        ' ORDER BY e.ts DESC,e.height DESC,e.idx DESC', args)
    items, links = [], []
    for row in rows:
        link = json.loads(row['link_value']) if row['link_value'] else None
        if link:links.append({**link,'amount':tokens(link['amount_raw'])})
        items.append({'event_id':row['id'],'tx_hash':row['tx_hash'],'log_index':row['idx'],
            'eth_address':row['src'],'eth_height':row['height'],'eth_ts':row['ts'],
            'amount_raw':row['amount_raw'],'amount':tokens(row['amount_raw']),
            'gnk_address':link['gnk_address'] if link else None,
            'status':'completed' if link else 'unverified','link':link,
            'error':row['error'],'retry_at':row['retry_at']})
    return {'total':len(items),'verified':len(links),'pending':len(items)-len(links),
            'items':items,'links':links,'amount_raw':str(sum(int(e['amount_raw']) for e in items)),
            'status':db.get('provenance:burns',{})}


def native_addresses(db):
    return [r[0] for r in db.conn.execute('''
        SELECT l.gnk_address FROM gonka_mint_links l JOIN wgnk_mints m
          ON m.tx_hash=l.tx_hash AND m.log_index=l.log_index WHERE m.finalized=1
        UNION SELECT l.gnk_address FROM gonka_burn_links l JOIN events e
          ON e.id=l.event_id WHERE e.chain='ethereum' AND e.kind='bridge_burn' AND e.finalized=1
    ''')]


async def collect_burns(collector):
    db = collector.db
    rows = db.conn.execute('''SELECT e.* FROM events e
        LEFT JOIN gonka_burn_links l ON l.event_id=e.id
        LEFT JOIN gonka_burn_attempts a ON a.event_id=e.id
        WHERE e.chain='ethereum' AND e.kind='bridge_burn' AND e.finalized=1 AND l.event_id IS NULL
          AND (a.retry_at IS NULL OR a.retry_at<=?) ORDER BY e.height DESC,e.idx DESC LIMIT 6''',
        (int(time.time()),)).fetchall()
    if not rows:return 20
    # Same public chain check as bank snapshots. Never accept another network.
    if time.time()-collector.balance_chain_checked>60:
        status=(await collector.request('/chain-rpc/status'))['result']
        if status['node_info']['network']!='gonka-mainnet' or status['sync_info']['catching_up']:
            raise ValueError('Источник обратного моста не синхронизирован с Gonka mainnet')
        collector.balance_chain_checked=int(time.time())
    for row in rows:
        try:
            index=burn_index(row)
            reply=await collector.request(PATH+str(row['height'])+'/'+str(index))
            save_burn_link(db,verify_burn(dict(row),reply))
        except asyncio.CancelledError:raise
        except Exception as error:
            old=db.conn.execute('SELECT attempts FROM gonka_burn_attempts WHERE event_id=?',(row['id'],)).fetchone()
            attempts=(old[0] if old else 0)+1
            with db.conn:
                db.conn.execute('INSERT OR REPLACE INTO gonka_burn_attempts VALUES(?,?,?,?)',
                    (row['id'],attempts,int(time.time())+min(3600,30*2**min(attempts,6)),str(error)[:240]))
    collector.owner.status('provenance:burns',retrying=db.conn.execute('SELECT COUNT(*) FROM gonka_burn_attempts').fetchone()[0])
    return .2
