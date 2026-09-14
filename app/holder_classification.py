"""Merged-balance flow classification; related flows never imply common ownership."""
import json
from collections import defaultdict
from .holder_groups import category
from .config import ZERO

BUY = '@purchased'
BANK_KINDS = {'transfer','bridge_lock','escrow_deposit','bridge_release','vesting_unlock',
              'vesting_funding','reward_paid','fee','collateral_deposit','collateral_release','module_transfer'}

def estimate(roots, native, ethereum, mints, burns, pools, modules=(), native_complete=False, native_balances=None):
    """After mixing, the common balance carries all associated flow origins.

    Trade references retain actual execution quantities, counted once per root.
    Thus a one-token purchase mixed with 100 tokens is still a one-token purchase.
    Root aggregates overlap and must not be added across addresses. Pools/modules
    are boundaries, not shared ownership groups. Native discovery stays partial.
    """
    roots=set(roots)
    native_balances=native_balances or {}
    balances=defaultdict(int); colors=defaultdict(set)
    purchases={}; bought_refs=defaultdict(set); sold_refs=defaultdict(set)
    result={a:dict(bought_raw=0,sold_raw=0,reward_raw=0,outgoing_raw=0,paths=[]) for a in roots}
    native=[dict(e) for e in native]
    ethereum=[dict(e) for e in ethereum]
    mint_map={(m['tx_hash'],m['log_index']):m for m in mints}
    burn_map={(b['tx_hash'],b['log_index']):b for b in burns}
    locked={}; returned={}
    # Known balances anchor the observed native ledger; missing events remain explicit.
    native_net=defaultdict(int); floors=defaultdict(int)
    for e in sorted(native,key=lambda e:(e['height'],e.get('trace_order',e['idx']))):
        if e['kind'] not in BANK_KINDS: continue
        q=int(e['amount_raw']);src,dst=e['src'],e['dst']
        native_net[src]-=q;floors[src]=min(floors[src],native_net[src]);native_net[dst]+=q
    for a in set(floors)|set(native_balances):
        opening=max(-floors[a],native_balances.get(a,0)-native_net[a])
        balances[('gonka',a)]=opening
        if a in roots and opening: colors[('gonka',a)].add(a)
    # Match transfers to single-direction swaps within a pool/transaction.
    trade=defaultdict(lambda:dict(buy=0,sell=0,incoming=0,outgoing=0,lp=False))
    for e in ethereum:
        t=trade[(e['tx_hash'],e['pool'])]
        meta=e['meta'] if isinstance(e['meta'],dict) else json.loads(e['meta'])
        if e['kind'] in ('buy','sell') and meta.get('attribution')=='initiator_net': t[e['kind']]+=int(e['amount_raw'])
        if e['kind'] in ('liquidity_add','liquidity_remove'): t['lp']=True
        if e['kind']=='transfer':
            if e['src'] in pools: trade[(e['tx_hash'],e['src'])]['outgoing']+=int(e['amount_raw'])
            if e['dst'] in pools: trade[(e['tx_hash'],e['dst'])]['incoming']+=int(e['amount_raw'])
    def associate(tags):
        references={key for key in tags if key in purchases}
        for origin in tags & roots:
            bought_refs[origin].update(references)
    def move(chain,src,dst,q,override=None):
        source=(chain,src);dest=(chain,dst)
        if chain=='ethereum' and src!=ZERO and balances[source]<q:
            raise ValueError('Incomplete Ethereum transfer ledger for holder tracing')
        available=max(balances[source],q)
        if chain=='gonka' and src in roots: colors[source].add(src)
        moved=set(colors[source]) if q else set()
        balances[source]=available-q
        if not balances[source]: colors[source].clear()
        if override is not None: moved=set(override)
        balances[dest]+=q
        colors[dest].update(moved)
        if chain=='gonka' and dst in roots: colors[dest].add(dst)
        associate(colors[dest])
        return moved
    events=native+ethereum
    events.sort(key=lambda e:(e['ts'],0 if e['chain']=='gonka' else 1,e['height'],
        e.get('trace_order',(e['meta'] if isinstance(e['meta'],dict) else json.loads(e['meta'])).get('transaction_index',0)),e['idx']))
    for e in events:
        chain,kind,src,dst=e['chain'],e['kind'],e['src'],e['dst'];q=int(e['amount_raw'])
        meta=e['meta'] if isinstance(e['meta'],dict) else json.loads(e['meta'])
        if chain=='gonka':
            if kind in ('reward_paid','reward_vested') and dst in roots and meta.get('mining_participant')==dst:
                result[dst]['reward_raw']+=q
            if kind not in BANK_KINDS: continue
            if src in roots and src!=dst and kind!='fee': result[src]['outgoing_raw']+=q
            injected=returned.pop(e.get('request_key',''),None) if kind=='bridge_release' else None
            if src in modules and kind!='bridge_release': injected={}
            moved=move(chain,src,dst,q,injected)
            if kind=='bridge_lock': locked[e['request_key']]=moved
            if dst in modules: colors[(chain,dst)].clear()
        elif kind=='bridge_mint':
            link=mint_map.get((e['tx_hash'],e['idx']))
            tags=set()
            if link:
                tags=set(locked.pop(link['request_id'],set()))
                if link['gnk_address'] in roots:
                    tags.add(link['gnk_address'])
                    result[link['gnk_address']]['has_bridge']=True
            move(chain,src,dst,q,tags)
        elif kind=='bridge_burn':
            moved=move(chain,src,dst,q,{}) if src==ZERO else move(chain,src,dst,q)
            colors[(chain,ZERO)].clear()
            link=burn_map.get((e['tx_hash'],e['idx']))
            if link:
                target=link['gnk_address']
                if target in roots:
                    associate(moved | {target})
                returned[e['request_key']]=moved
        elif kind=='transfer':
            override=None
            if src in pools:
                t=trade[(e['tx_hash'],src)]
                override=set()
                if not t['lp'] and not t['sell'] and t['buy']==t['outgoing'] and not t['incoming']:
                    reference=BUY+e['id'];purchases[reference]=q;override.add(reference)
            moved=move(chain,src,dst,q,override)
            if dst in pools:
                t=trade[(e['tx_hash'],dst)]
                if not t['lp'] and not t['buy'] and t['sell']==t['incoming'] and not t['outgoing']:
                    for a in moved & roots:
                        if e['id'] not in sold_refs[a]:
                            sold_refs[a].add(e['id']);result[a]['sold_raw']+=q
                            if len(result[a]['paths'])<5: result[a]['paths'].append(e['tx_hash'])
                colors[(chain,dst)].clear()
    for a,row in result.items():
        row['bought_raw']=sum(purchases[reference] for reference in bought_refs[a])
        row['category']=category(row['bought_raw'],row['sold_raw']) if row['bought_raw']+row['sold_raw'] else 'mining_pending' if row['reward_raw'] and not row['outgoing_raw'] and not row.get('has_bridge') else 'unknown'
        row.update(estimated=True,history_complete=False,flow_scope='merged_balances',
                   classification_note='По общему потоку найденных цепочек GNK → мост → WGNK. После смешивания баланс считается единым: дальнейшие исполнения учитываются целиком, без пропорционального деления. Каждое исполнение считается один раз для исходного адреса. Объёмы разных исходных адресов могут пересекаться; это не личные сделки и не доказательство общего владельца. Пулы и системные модули не объединяют потоки. История GNK ещё неполная.')
        if row['reward_raw'] > 0 and row['sold_raw'] * 10 < row['reward_raw']:
            row.update(category='miners', history_complete=native_complete,
                classification_note='Майнер: подтверждены награды самому участнику, а продажи связанного потока составляют строго менее 10% суммы этих наград. Ровно 10% не подходит. Переводы и мост сами по себе не являются продажами. Расчёт по найденной истории; она ещё может дополняться.')
        row['miner_reward_basis_raw']=str(row['reward_raw'])
        for k in ('bought_raw','sold_raw','reward_raw','outgoing_raw'): row[k]=str(row[k])
    return result
