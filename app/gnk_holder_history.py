"""Public, targeted GNK holder history. Indexed discoveries are never chain coverage."""
import json
import asyncio
import time
from .codec import attr, parse_native
from .holders import GNK_ADDRESS

SCHEMA = """
CREATE TABLE IF NOT EXISTS holder_native_targets(address TEXT PRIMARY KEY, depth INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS holder_native_blocks(height INTEGER PRIMARY KEY, checked_at INTEGER, retry_at INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS holder_native_balances(address TEXT PRIMARY KEY,height INTEGER NOT NULL,amount_raw TEXT,retry_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS holder_native_events(id TEXT PRIMARY KEY,height INTEGER NOT NULL,value TEXT NOT NULL);
"""
MAX_TARGETS = 5000
MAX_DEPTH = 6

def initialize(db):
    db.conn.executescript(SCHEMA)

def targets(db, modules=None):
    modules = modules or {}
    seeds = [r[0] for r in db.conn.execute('SELECT address FROM public_gnk_holders') if r[0] not in modules]
    with db.conn:
        db.conn.executemany('INSERT INTO holder_native_targets VALUES(?,0) ON CONFLICT(address) DO UPDATE SET depth=0', [(a,) for a in seeds])
    return [r[0] for r in db.conn.execute('SELECT address FROM holder_native_targets ORDER BY depth,address') if r[0] not in modules]

def discover(db, address, rows, modules):
    """Called inside validated page transaction: only enqueue successful monetary txs."""
    from .provenance import HASH, coins
    depth_row = db.conn.execute('SELECT depth FROM holder_native_targets WHERE address=?',(address,)).fetchone()
    depth = depth_row[0] if depth_row else 0
    total = db.conn.execute('SELECT count(*) FROM holder_native_targets').fetchone()[0]
    for tx in rows:
        if tx.get('success') is not True:
            continue
        if not HASH.fullmatch(tx.get('tx_hash','')):
            raise ValueError('Invalid holder history transaction')
        events = tx['events']
        if isinstance(events,str): events=json.loads(events)
        relevant=False
        for event in events:
            a=attr(event)
            if event['type']=='transfer' and coins(a.get('amount','')):
                src,dst=a.get('sender',''),a.get('recipient','')
                if address not in (src,dst): continue
                relevant=True
                if src==address and dst!=address and GNK_ADDRESS.fullmatch(dst) and dst not in modules:
                    existing=db.conn.execute('SELECT depth FROM holder_native_targets WHERE address=?',(dst,)).fetchone()
                    if existing:
                        if existing[0]>depth+1: db.conn.execute('UPDATE holder_native_targets SET depth=? WHERE address=?',(depth+1,dst))
                    elif depth<MAX_DEPTH and total<MAX_TARGETS:
                        inserted=db.conn.execute('INSERT OR IGNORE INTO holder_native_targets VALUES(?,?)',(dst,depth+1))
                        total+=inserted.rowcount
                    else:
                        from .holders import metadata
                        metadata(db,'holder_history:limited',True)
            elif event['type']=='vest_reward' and a.get('participant')==address:
                relevant=True
        if relevant:
            db.conn.execute('INSERT OR IGNORE INTO holder_native_blocks(height) VALUES(?)',(int(tx['block_height']),))

def progress(db):
    rows=db.conn.execute('SELECT count(*),count(checked_at) FROM holder_native_blocks').fetchone()
    count=db.conn.execute('SELECT count(*) FROM holder_native_targets').fetchone()[0]
    return dict(addresses=count,blocks=rows[0],verified_blocks=rows[1],
                complete=False,limited=bool(db.get('holder_history:limited',False)),
                source='indexed discovery; targeted native block/results verification')

class HolderHistory:
    # Recomputing labels is the heaviest routine job; on a shared free CPU it must
    # run only after verification actually added blocks, and never more often than
    # this. HOLDERS_LABELS_ENABLED=false idles the whole loop.
    CLASSIFY_MIN_INTERVAL = 600

    def __init__(self,owner):
        self.owner,self.db=owner,owner.db
        initialize(self.db)
        self.last_estimate=0
        self.verified_signature=None
        self.history_turn=False
        if not self.db.get('holder_history:version'):
            # Existing incoming-only caches need a new pass to discover outgoing txs.
            with self.db.conn:
                for row in self.db.conn.execute('SELECT address,value FROM gonka_address_sync').fetchall():
                    state=json.loads(row[1]);state.update(offset=0,anchor=None,head=None,phase='history',exhausted=False,next_check=0)
                    self.db.conn.execute('UPDATE gonka_address_sync SET value=? WHERE address=?',(json.dumps(state),row[0]))
                self.db.conn.execute('INSERT OR IGNORE INTO holder_native_blocks(height) SELECT DISTINCT height FROM gonka_incoming')
            self.db.put('holder_history:version',1)

    async def maybe_classify(self):
        if time.monotonic()-self.last_estimate < self.CLASSIFY_MIN_INTERVAL:
            return
        rows=self.db.conn.execute(
            'SELECT count(*),count(checked_at),coalesce(max(checked_at),0) FROM holder_native_blocks').fetchone()
        signature=tuple(rows)
        if self.verified_signature is not None and signature==self.verified_signature:
            return
        await self.classify()
        self.verified_signature=signature
        self.last_estimate=time.monotonic()

    async def balance(self):
        snapshot=self.db.get('public_holders:GNK')
        if not snapshot: return
        now=int(time.time());height=snapshot['height']
        row=self.db.conn.execute("""SELECT t.address FROM holder_native_targets t
            LEFT JOIN public_gnk_holders h ON h.address=t.address
            LEFT JOIN holder_native_balances b ON b.address=t.address
            WHERE h.address IS NULL AND (b.address IS NULL OR (b.retry_at<=? AND (b.height!=? OR b.amount_raw IS NULL)))
            ORDER BY t.depth,t.address LIMIT 1""",(now,height)).fetchone()
        if not row: return
        address=row[0]
        try:
            data,returned_height=await self.owner.net.api('/cosmos/bank/v1beta1/balances/'+address+'/by_denom',{'denom':'ngonka'},height=height,with_height=True)
            coin=data['balance']
            if returned_height!=height or coin['denom']!='ngonka' or str(int(coin['amount']))!=coin['amount'] or int(coin['amount'])<0:
                raise ValueError('Intermediate GNK balance snapshot mismatch')
            with self.db.conn:
                self.db.conn.execute('INSERT OR REPLACE INTO holder_native_balances VALUES(?,?,?,0)',(address,height,coin['amount']))
        except Exception:
            with self.db.conn:
                self.db.conn.execute('INSERT OR REPLACE INTO holder_native_balances VALUES(?,?,NULL,?)',(address,height,now+600))

    async def classify(self):
        from .flows import market_packet, market_rows, history_end, balances
        from .holder_classification import estimate
        snapshot=self.db.get('public_holders:GNK')
        packet=market_packet(self.db)
        eth=packet['snapshot'] if packet else self.db.get('flow:snapshot')
        start=self.db.get('mints:deployment',{}).get('height')
        if not snapshot or not eth or not start or not eth.get('ledger_verified'): return
        if not packet and history_end(self.db,start,eth['height'])!=eth['height']: return
        ethereum=[dict(e) for e in market_rows(self.db,start,eth,packet)]
        ledger,minted,burned=balances(ethereum)
        if any(n<0 for n in ledger.values()) or sum(ledger.values())!=int(eth['supply_raw']) or minted-burned!=int(eth['supply_raw']): return
        modules=self.owner.provenance.modules or {}
        roots=[r[0] for r in self.db.conn.execute('SELECT address FROM public_gnk_holders') if r[0] not in modules]
        ranges=self.db.conn.execute("SELECT lo,hi FROM ranges WHERE chain='gonka' ORDER BY lo").fetchall()
        through=0
        for lo,hi in ranges:
            if lo>through+1: break
            through=max(through,hi)
        native_complete=through>=snapshot['height']
        native=[json.loads(r[0]) for r in self.db.conn.execute('SELECT value FROM holder_native_events WHERE height<=?',(snapshot['height'],))]
        if native_complete:
            native=[dict(r) for r in self.db.conn.execute("SELECT * FROM events WHERE chain='gonka' AND finalized=1 AND height<=?",(snapshot['height'],))]
        mints=[json.loads(r[0]) for r in self.db.conn.execute('SELECT value FROM gonka_mint_links')]
        mints=[m for m in mints if m['gnk_height']<=snapshot['height'] and m['eth_height']<=eth['height']]
        burns=[json.loads(r[0]) for r in self.db.conn.execute('SELECT value FROM gonka_burn_links')]
        burns=[b for b in burns if b['eth_height']<=eth['height'] and b['eth_ts']<=snapshot['ts']]
        native_balances={r[0]:int(r[1]) for r in self.db.conn.execute('SELECT address,balance_raw FROM public_gnk_holders')}
        native_balances.update({r[0]:int(r[1]) for r in self.db.conn.execute('SELECT address,amount_raw FROM holder_native_balances WHERE height=? AND amount_raw IS NOT NULL',(snapshot['height'],))})
        values=await asyncio.to_thread(estimate,roots,native,ethereum,mints,burns,{p['address'] for p in eth['pools']},set(modules),native_complete,native_balances)
        for address,name in modules.items():
            values[address]=dict(category='module',classification_note='Системный модуль Gonka: '+name,estimated=False)
        self.db.put('holder_history:classification',dict(items=values,gnk_height=snapshot['height'],eth_height=eth['height'],checked_at=int(time.time())))

    async def run(self):
        collector=self.owner.provenance
        if collector.modules is None: return 5
        if not getattr(self.owner.cfg,'holders_labels_enabled',True):
            return 600
        await self.maybe_classify()
        now=int(time.time())
        self.history_turn=not self.history_turn
        direction='ASC' if self.history_turn else 'DESC'
        # Blocks carrying a public holder's own transfers come first: visible
        # classifications must not wait behind deep counterparty discovery.
        root_blocks={r[0] for r in self.db.conn.execute(
            'SELECT DISTINCT height FROM gonka_incoming WHERE address IN (SELECT address FROM public_gnk_holders)')}
        pending=[r[0] for r in self.db.conn.execute(
            'SELECT height FROM holder_native_blocks WHERE checked_at IS NULL AND retry_at<=? ORDER BY height '+direction+' LIMIT 400',(now,))]
        pending.sort(key=lambda h:(h not in root_blocks,h if direction=='ASC' else -h))
        rows=pending[:5]
        for offset in range(0,len(rows),5):
            heights=rows[offset:offset+5]
            try:
                # One batched JSON-RPC packet per five blocks instead of five round trips.
                await collector.native_blocks(heights)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                with self.db.conn:
                    self.db.conn.execute(
                        'UPDATE holder_native_blocks SET retry_at=?,error=? WHERE height IN ('+','.join('?'*len(heights))+')',
                        (now+600,str(error)[:200],*heights))
                continue
            for height in heights:
                try:
                    block,reply=collector.blocks[height]
                    header,events=parse_native(block,reply,collector.modules)
                    with self.db.conn:
                        for index,event in enumerate(events):
                            event["trace_order"]=index
                            value=json.dumps(event,sort_keys=True)
                            old=self.db.conn.execute('SELECT value FROM holder_native_events WHERE id=?',(event['id'],)).fetchone()
                            if old and old[0]!=value: raise ValueError('Native holder history changed')
                            self.db.conn.execute('INSERT OR IGNORE INTO holder_native_events VALUES(?,?,?)',(event['id'],height,value))
                        self.db.conn.execute('UPDATE holder_native_blocks SET checked_at=?,error=NULL WHERE height=?',(now,height))
                    self.db.revision+=1
                except Exception as error:
                    with self.db.conn:
                        self.db.conn.execute('UPDATE holder_native_blocks SET retry_at=?,error=? WHERE height=?',(now+600,str(error)[:200],height))
        await self.balance()
        self.db.put('holder_history:progress',progress(self.db))
        return 3 if rows else 20
