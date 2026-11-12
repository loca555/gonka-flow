import unittest
from types import SimpleNamespace
from app.liquid_supply import LiquidSupply

class LiquidTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_vesting_subtraction_and_failure_preserves_snapshot(self):
        saved={}
        class Net:
            wrong=False
            async def api(self,path,params=None,**kwargs):
                if path.endswith('/latest'): return {'block':{'header':{'height':'10','chain_id':'gonka-mainnet','time':'2026-10-04T00:00:00Z'}}}
                if 'supply/by_denom' in path: data={'amount':{'denom':'ngonka','amount':'1000'}}
                elif path.endswith('/accounts'): data={'accounts':[{'@type':'/cosmos.vesting.v1beta1.ContinuousVestingAccount','base_vesting_account':{'base_account':{'address':'a'}}}], 'pagination':{}}
                elif 'spendable' in path: data={'balances':[{'denom':'ngonka','amount':'100'}]}
                elif path.endswith('/module_accounts'): data={'accounts':[{'name':'streamvesting','base_account':{'address':'s'}}]}
                else: data={'balance':{'denom':'ngonka','amount':'300' if '/a/' in path else '50'}}
                return data, 9 if self.wrong else 10
        net=Net(); collector=LiquidSupply(SimpleNamespace(net=net,db=SimpleNamespace(get=saved.get,put=lambda k,v:saved.update({k:v}))))
        await collector.run()
        self.assertEqual(saved['liquid_supply:GNK']['liquid_raw'],'750')
        self.assertEqual(saved['liquid_supply:GNK']['account_vesting_raw'],'200')
        saved['liquid_supply:GNK']['checked_at']=0
        before=saved['liquid_supply:GNK'].copy(); net.wrong=True
        with self.assertRaises(ValueError): await collector.run()
        self.assertEqual(saved['liquid_supply:GNK'],before)

    async def test_resume_pages_and_cached_refresh(self):
        import time
        saved={}
        class Net:
            keys=[]
            fail=True
            async def api(self,path,params=None,**kwargs):
                if path.endswith('/latest'):
                    return {'block':{'header':{'height':'10','chain_id':'gonka-mainnet','time':'now'}}}
                if path.endswith('/accounts'):
                    key=params.get('pagination.key'); self.keys.append(key)
                    if key=='next' and self.fail: raise RuntimeError('temporary RPC failure')
                    data={'accounts':[], 'pagination':{'next_key':'next' if key is None else None}}
                elif 'supply/by_denom' in path: data={'amount':{'denom':'ngonka','amount':'1000'}}
                elif path.endswith('/module_accounts'): data={'accounts':[{'name':'streamvesting','base_account':{'address':'s'}}]}
                else: data={'balance':{'denom':'ngonka','amount':'50'}}
                return data,10
        net=Net(); db=SimpleNamespace(get=saved.get,put=lambda k,v:saved.update({k:v}))
        with self.assertRaises(RuntimeError): await LiquidSupply(SimpleNamespace(net=net,db=db)).run()
        self.assertEqual(saved['liquid_supply:discovery']['key'],'next')
        self.assertNotIn('liquid_supply:GNK',saved)
        net.fail=False
        await LiquidSupply(SimpleNamespace(net=net,db=db)).run()
        self.assertEqual(net.keys,[None,'next','next'])
        self.assertEqual(saved['liquid_supply:GNK']['liquid_raw'],'950')
        saved['liquid_supply:GNK']['checked_at']=0
        await LiquidSupply(SimpleNamespace(net=net,db=db)).run()
        self.assertEqual(net.keys,[None,'next','next'])
        self.assertGreater(saved['liquid_supply:GNK']['checked_at'],0)
        saved['liquid_supply:catalog']['checked_at']=int(time.time())-86401
        await LiquidSupply(SimpleNamespace(net=net,db=db)).run()
        self.assertEqual(net.keys[-2:],[None,'next'])
