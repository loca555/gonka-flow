"""Pinned Bank supply minus non-spendable vesting; never substitute failures with zero."""
import time

def amount(coins):
    return sum(int(c['amount']) for c in coins if c['denom']=='ngonka')

class LiquidSupply:
    def __init__(self, indexer): self.net=indexer.net; self.db=indexer.db; self.cfg=getattr(indexer,'cfg',None)

    async def run(self):
        if self.cfg is None: return await self.collect()
        from dataclasses import replace
        from .sources import Sources
        self.net=Sources(replace(self.cfg,gonka=list(dict.fromkeys([*self.cfg.gonka_rpc,*self.cfg.gonka]))))
        try: return await self.collect()
        finally: await self.net.close()

    async def collect(self):
        block=await self.net.api('/cosmos/base/tendermint/v1beta1/blocks/latest')
        header=block['block']['header']; height=int(header['height'])
        if header['chain_id']!='gonka-mainnet': raise ValueError('Wrong supply chain')
        async def query(path,params=None):
            data,served=await self.net.api(path,params,height=height,with_height=True)
            if served!=height: raise ValueError('Supply block mismatch')
            return data
        now=int(time.time())
        catalog=self.db.get('liquid_supply:catalog')
        snapshot=self.db.get('liquid_supply:GNK')
        # Refresh balances independently of the slow, resumable discovery pass.
        if catalog and (not snapshot or now-snapshot['checked_at']>=600
                        or snapshot.get('catalog_height')!=catalog['height']):
            await self.refresh(query,header,catalog)
        state=self.db.get('liquid_supply:discovery')
        if not state and (not catalog or now-catalog['checked_at']>=86400):
            state={'height':height,'addresses':[],'key':None,'seen':[],
                   'accounts_scanned':0,'started_at':now}
        if state:
            async def discovery_query(params):
                data,served=await self.net.api('/cosmos/auth/v1beta1/accounts',params,
                                               height=state['height'],with_height=True)
                if served!=state['height']: raise ValueError('Discovery block mismatch')
                return data
            # Persist only fully validated pages; errors/restarts resume the same cursor.
            for _ in range(10):
                params={'pagination.limit':'1000'}
                if state['key']: params['pagination.key']=state['key']
                data=await discovery_query(params)
                addresses=list(state['addresses'])
                for account in data['accounts']:
                    if 'Vesting' not in account.get('@type',''): continue
                    base=account.get('base_vesting_account')
                    if not base: raise ValueError('Unsupported vesting account')
                    addr=base['base_account']['address']
                    if addr in addresses: raise ValueError('Duplicate vesting account')
                    addresses.append(addr)
                key=data.get('pagination',{}).get('next_key')
                if key and key in state['seen']: raise ValueError('Account pagination cycle')
                state={**state,'addresses':addresses,'key':key,
                       'seen':state['seen']+([key] if key else []),
                       'accounts_scanned':state['accounts_scanned']+len(data['accounts'])}
                self.db.put('liquid_supply:progress',{'height':state['height'],
                    'accounts_scanned':state['accounts_scanned'],'checked_at':int(time.time())})
                if not key:
                    catalog={'height':state['height'],'addresses':addresses,
                             'accounts_scanned':state['accounts_scanned'],'checked_at':int(time.time())}
                    self.db.put('liquid_supply:catalog',catalog)
                    self.db.put('liquid_supply:discovery',None)
                    # Latest pinned balances, even when discovery took hours.
                    if not snapshot: await self.refresh(query,header,catalog)
                    return 1
                self.db.put('liquid_supply:discovery',state)
            return 1
        return 60

    async def refresh(self,query,header,catalog):
        supply=await query('/cosmos/bank/v1beta1/supply/by_denom',{'denom':'ngonka'})
        if supply['amount']['denom']!='ngonka': raise ValueError('Wrong supply denom')
        total=int(supply['amount']['amount']); locked=0
        addresses=catalog['addresses']
        for addr in addresses:
            balance=await query('/cosmos/bank/v1beta1/balances/'+addr+'/by_denom',{'denom':'ngonka'})
            spendable=await query('/cosmos/bank/v1beta1/spendable_balances/'+addr,{'pagination.limit':'1000'})
            if spendable.get('pagination',{}).get('next_key'): raise ValueError('Incomplete spendable balances')
            value=int(balance['balance']['amount'])-amount(spendable['balances'])
            if value<0: raise ValueError('Invalid locked balance')
            locked+=value
        modules=await query('/cosmos/auth/v1beta1/module_accounts')
        vesting=next(a for a in modules['accounts'] if a['name']=='streamvesting')
        data=await query('/cosmos/bank/v1beta1/balances/'+vesting['base_account']['address']+'/by_denom',{'denom':'ngonka'})
        stream=int(data['balance']['amount'])
        liquid=total-locked-stream
        if not 0<=liquid<=total: raise ValueError('Supply does not reconcile')
        self.db.put('liquid_supply:GNK',{'height':int(header['height']),'block_time':header['time'],'checked_at':int(time.time()),
            'bank_supply_raw':str(total),'account_vesting_raw':str(locked),'streamvesting_raw':str(stream),
            'liquid_raw':str(liquid),'vesting_accounts':len(addresses),
            'catalog_height':catalog['height'],'catalog_checked_at':catalog['checked_at'],
            'catalog_accounts_scanned':catalog['accounts_scanned'],
            'coverage':'periodic_account_discovery','discovery_interval_seconds':86400,
            'method':'bank_minus_account_locked_and_streamvesting','refresh_seconds':600})
        return 600
