"""Public holder snapshots: exact balances, threshold 10,000, no private labels."""
import time
import json
from datetime import datetime
from .config import ZERO
from .holders import GNK_ADDRESS, ETH_ADDRESS, metadata
from .holder_groups import category
from .flows import market_packet, market_rows, history_end, balances
from .timezones import TIME_ZONE

MINIMUM = 10_000
MINIMUM_RAW = MINIMUM * 10**9
REFRESH_SECONDS = 21600
SCHEMA = """
CREATE TABLE IF NOT EXISTS public_gnk_holders(address TEXT PRIMARY KEY, balance_raw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS public_gnk_holder_stage(address TEXT PRIMARY KEY, balance_raw TEXT NOT NULL);
"""

def initialize(db):
    db.conn.executescript(SCHEMA)
    from .gnk_holder_history import initialize as initialize_history
    initialize_history(db)

def listing(db, asset, query='', limit=25, offset=0):
    result = dict(asset=asset, minimum=MINIMUM, ready=False, items=[], total=None,
                  sum_raw=None, supply_raw=None, offset=offset, limit=limit, has_more=False,
                  snapshot=None, timezone=TIME_ZONE, progress=None, collector=None)
    if asset == 'WGNK':
        packet = market_packet(db)
        snapshot = packet['snapshot'] if packet else db.get('flow:snapshot')
        start = db.get('mints:deployment', {}).get('height')
        result['collector'] = db.get('flow:status', {})
        if not start or not snapshot or not snapshot.get('ledger_verified'):
            return result
        if not packet and history_end(db, start, snapshot['height']) != snapshot['height']:
            return result
        rows = market_rows(db, start, snapshot, packet)
        ledger, minted, burned = balances(rows)
        supply = int(snapshot['supply_raw'])
        if (any(raw < 0 for raw in ledger.values()) or ledger.get(ZERO, 0) != 0
                or sum(ledger.values()) != supply or minted - burned != supply):
            return result
        pools = {p['address'] for p in snapshot['pools']}
        activity = {}
        for event in rows:
            if (event['kind'] not in ('buy', 'sell') or event['pool'] not in pools
                    or not ETH_ADDRESS.fullmatch(event['actor'] or '') or event['actor'] == ZERO
                    or json.loads(event['meta']).get('attribution') != 'initiator_net'):
                continue
            volume = activity.setdefault(event['actor'], {'buy': 0, 'sell': 0})
            volume[event['kind']] += int(event['amount_raw'])
        items = []
        for address, raw in ledger.items():
            if raw < MINIMUM_RAW:
                continue
            volume = activity.get(address, {'buy': 0, 'sell': 0})
            items.append(dict(address=address, balance_raw=str(raw), pool=address in pools,
                              category='pool' if address in pools else category(volume['buy'], volume['sell']),
                              bought_raw=str(volume['buy']), sold_raw=str(volume['sell'])))
        result['classification'] = dict(period='all_history', threshold='strictly_more_than_90_percent',
                                        scope='verified_WGNK_swaps', height=snapshot['height'])
        result['snapshot'] = {k: snapshot[k] for k in ('height', 'ts', 'hash')}
    else:
        snapshot = db.get('public_holders:GNK')
        scan = db.get('public_holders:GNK:scan')
        result['progress'] = ({k: scan[k] for k in ('seen', 'total', 'height')} if scan else None)
        result['collector'] = db.get('public_holders:GNK:status', {})
        if not snapshot:
            return result
        classifications=db.get('holder_history:classification',{})
        labels=classifications.get('items',{})
        items = [dict(**dict(r), **labels.get(r['address'],dict(category='unknown',classification_note='Проверяем историю переводов GNK и операции на стороне WGNK.')))
                 for r in db.conn.execute('SELECT address,balance_raw FROM public_gnk_holders')]
        result['classification']={k:v for k,v in classifications.items() if k!='items'}
        result['history_progress']=db.get('holder_history:progress')
        supply = int(snapshot['supply_raw'])
        result['snapshot'] = snapshot
    items.sort(key=lambda r: (-int(r['balance_raw']), r['address']))
    for rank, item in enumerate(items, 1):
        item['rank'] = rank
        item['share_bps'] = int(item['balance_raw']) * 10000 // supply if supply else 0
    result.update(ready=True, total_holders=len(items), supply_raw=str(supply),
                  sum_raw=str(sum(int(r['balance_raw']) for r in items)))
    selected = [r for r in items if query.lower() in r['address']]
    result.update(total=len(selected), items=selected[offset:offset+limit],
                  has_more=offset+limit<len(selected))
    return result

class NativeHolders:
    """Read every bank balance at one pinned block; retain only balances >=10k.

    All rows contribute to exact supply/count reconciliation. A failed page never
    advances the cursor. Only a fully reconciled scan replaces the visible list.
    """
    def __init__(self, owner):
        self.db, self.net = owner.db, owner.net
        initialize(self.db)

    async def run(self):
        db = self.db
        scan = db.get('public_holders:GNK:scan')
        current = db.get('public_holders:GNK', {})
        if not scan:
            if time.time() - current.get('checked_at', 0) < REFRESH_SECONDS:
                return 60
            scan = dict(height=None, key=None, keys=[], seen=0, total=0, sum_raw='0',
                        started_at=int(time.time()), done=False)
            with db.conn:
                db.conn.execute('DELETE FROM public_gnk_holder_stage')
        if scan['done']:
            try:
                return await self.finish(scan)
            except Exception:
                if time.time() - scan['started_at'] > 3600:
                    db.put('public_holders:GNK:scan', None)
                raise
        params = {'pagination.limit': '10000'}
        if scan['key']:
            params['pagination.key'] = scan['key']
        else:
            params['pagination.count_total'] = 'true'
        try:
            data, height = await self.net.api('/cosmos/bank/v1beta1/denom_owners/ngonka',
                                             params, height=scan['height'], with_height=True)
        except Exception:
            if time.time() - scan['started_at'] > 3600:
                db.put('public_holders:GNK:scan', None)
            raise
        if scan['height'] is not None and height != scan['height']:
            raise ValueError('GNK holder page changed snapshot height')
        total = scan['total'] if scan['height'] is not None else int(data['pagination']['total'])
        owners, key = data['denom_owners'], data['pagination'].get('next_key')
        if key and (key in scan['keys'] or not owners):
            raise ValueError('Repeated or empty GNK holder page')
        page_sum, selected, seen_addresses = 0, [], set()
        for row in owners:
            address, balance = row['address'], row['balance']
            raw = int(balance['amount'])
            if (not GNK_ADDRESS.fullmatch(address) or address in seen_addresses
                    or balance['denom'] != 'ngonka' or raw <= 0 or str(raw) != balance['amount']):
                raise ValueError('Invalid GNK holder balance')
            seen_addresses.add(address)
            page_sum += raw
            if raw >= MINIMUM_RAW:
                selected.append((address, str(raw)))
        seen = scan['seen'] + len(owners)
        if seen > total or (not key and seen != total):
            raise ValueError('GNK holder count does not reconcile')
        updated = {**scan, 'height': height, 'total': total, 'key': key,
                   'keys': scan['keys'] + ([key] if key else []), 'seen': seen,
                   'sum_raw': str(int(scan['sum_raw']) + page_sum), 'done': not key}
        with db.conn:
            db.conn.executemany('INSERT INTO public_gnk_holder_stage VALUES(?,?)', selected)
            metadata(db, 'public_holders:GNK:scan', updated)
        db.revision += 1
        return 1

    async def finish(self, scan):
        data, height = await self.net.api('/cosmos/bank/v1beta1/supply/by_denom',
                    {'denom': 'ngonka'}, height=scan['height'], with_height=True)
        if (height != scan['height'] or data['amount']['denom'] != 'ngonka'
                or int(data['amount']['amount']) != int(scan['sum_raw'])):
            self.db.put('public_holders:GNK:scan', None)
            raise ValueError('GNK holder sum does not match supply')
        block = await self.net.api('/cosmos/base/tendermint/v1beta1/blocks/' + str(height))
        header = block['block']['header']
        if header['chain_id'] != 'gonka-mainnet' or int(header['height']) != height:
            raise ValueError('GNK holder snapshot belongs to another chain/block')
        snapshot = dict(height=height, ts=int(datetime.fromisoformat(header['time'].replace('Z', '+00:00')).timestamp()),
                        checked_at=int(time.time()), supply_raw=scan['sum_raw'], scanned=scan['seen'],
                        source='cosmos.bank.denom_owners; pinned block; supply reconciled')
        with self.db.conn:
            self.db.conn.execute('DELETE FROM public_gnk_holders')
            self.db.conn.execute('INSERT INTO public_gnk_holders SELECT * FROM public_gnk_holder_stage')
            metadata(self.db, 'public_holders:GNK', snapshot)
            metadata(self.db, 'public_holders:GNK:scan', None)
            self.db.conn.execute('DELETE FROM public_gnk_holder_stage')
        self.db.revision += 1
        return 60
