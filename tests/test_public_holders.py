import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.config import Settings, ZERO
from app.codec import blank
from app.db import Database
from app.main import create_app
from app.mints import initialize as init_mints
from app.public_holders import NativeHolders, listing, MINIMUM_RAW

A, B, C = ('0x'+c*40 for c in '123')
G, H, J = ('gonka1'+c*38 for c in 'qpz')
BLOCK = {'height':1,'hash':'0x'+'a'*64,'ts':1789370000}

class PublicHoldersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/'test.sqlite3');init_mints(self.db)
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def seed_wgnk(self):
        amounts=[MINIMUM_RAW,MINIMUM_RAW-1,2**100+7]
        rows=[blank('ethereum',BLOCK,'0x'+'b'*64,i,'bridge_mint',raw,ZERO,a)
              for i,(a,raw) in enumerate(zip([A,B,C],amounts))]
        self.db.save_batch('ethereum',1,1,rows,[BLOCK])
        self.db.put('mints:deployment',BLOCK)
        self.db.put('flow:snapshot',{**BLOCK,'ledger_verified':True,'supply_raw':str(sum(amounts)),
                                    'pools':[{'address':C}]})
        return amounts
    def test_threshold_exact_sort_search_and_pagination(self):
        amounts=self.seed_wgnk()
        result=listing(self.db,'WGNK',limit=1)
        self.assertTrue(result['ready']);self.assertEqual(result['total'],2)
        self.assertEqual(result['items'][0]['address'],C);self.assertTrue(result['items'][0]['pool'])
        self.assertEqual(result['sum_raw'],str(amounts[0]+amounts[2]));self.assertTrue(result['has_more'])
        self.assertEqual(listing(self.db,'WGNK',offset=1)['items'][0]['balance_raw'],str(MINIMUM_RAW))
        self.assertEqual(listing(self.db,'WGNK',query=B)['total'],0)
        found=listing(self.db,'WGNK',query=A.upper())['items'][0]
        self.assertEqual(found['rank'],2);self.assertNotIn('label',found)
    def test_missing_ranges_or_invalid_supply_never_look_empty(self):
        self.seed_wgnk();self.db.conn.execute('DELETE FROM ranges');self.db.conn.commit()
        result=listing(self.db,'WGNK');self.assertFalse(result['ready']);self.assertIsNone(result['total'])
        self.db._range('ethereum',1,1);self.db.conn.commit()
        snap=self.db.get('flow:snapshot');snap['supply_raw']='1';self.db.put('flow:snapshot',snap)
        self.assertFalse(listing(self.db,'WGNK')['ready'])
    def test_live_packet_is_used(self):
        self.seed_wgnk();snap=self.db.get('flow:snapshot')
        newer={**snap,'height':2,'hash':'0x'+'c'*64}
        transfer=blank('ethereum',newer,'0x'+'d'*64,0,'transfer',MINIMUM_RAW,A,B)
        transfer['meta']='{}'
        self.db.put('flow:live',{'current':{'snapshot':newer,'base':1,'events':[transfer]},'history':[]})
        result=listing(self.db,'WGNK');self.assertEqual(result['snapshot']['height'],2)
        self.assertEqual([r['address'] for r in result['items']],[C,B])
    def test_native_scan_is_atomic_resumable_and_exact(self):
        db=self.db;huge=2**100+9
        class Net:
            fail=True
            async def api(net,path,params=None,**kw):
                if 'blocks/' in path:
                    return {'block':{'header':{'height':'20','chain_id':'gonka-mainnet','time':'2026-09-14T10:00:00Z'}}}
                if 'supply' in path:
                    if net.fail:net.fail=False;raise RuntimeError('temporary failure')
                    return {'amount':{'denom':'ngonka','amount':str(huge+MINIMUM_RAW+MINIMUM_RAW-1)}},20
                if params.get('pagination.key'):
                    self.assertEqual(kw['height'],20)
                    return {'denom_owners':[{'address':J,'balance':{'denom':'ngonka','amount':str(huge)}}],
                            'pagination':{'next_key':None,'total':'0'}},20
                return {'denom_owners':[{'address':G,'balance':{'denom':'ngonka','amount':str(MINIMUM_RAW)}},
                                       {'address':H,'balance':{'denom':'ngonka','amount':str(MINIMUM_RAW-1)}}],
                        'pagination':{'next_key':'next','total':'3'}},20
        owner=SimpleNamespace(db=db,net=Net())
        async def run():
            c=NativeHolders(owner)
            await c.run()
            self.assertIsNone(listing(db,'GNK')['total'])
            self.assertEqual(db.conn.execute('SELECT count(*) FROM public_gnk_holder_stage').fetchone()[0],1)
            await c.run()
            with self.assertRaises(RuntimeError):await c.run()
            self.assertFalse(listing(db,'GNK')['ready'])
            await NativeHolders(owner).run()
        asyncio.run(run());result=listing(db,'GNK')
        self.assertTrue(result['ready']);self.assertEqual(result['total'],2)
        self.assertEqual(result['items'][0]['balance_raw'],str(huge))
        self.assertEqual(result['snapshot']['scanned'],3)
        self.assertIsNone(result['progress'])
    def test_bad_page_does_not_advance_cursor_or_visible_data(self):
        class Net:
            async def api(net,*args,**kw):
                return {'denom_owners':[{'address':G,'balance':{'denom':'bad','amount':'1'}}],
                        'pagination':{'next_key':None,'total':'1'}},20
        c=NativeHolders(SimpleNamespace(db=self.db,net=Net()))
        with self.assertRaises(ValueError):asyncio.run(c.run())
        self.assertIsNone(self.db.get('public_holders:GNK:scan'))
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM public_gnk_holder_stage').fetchone()[0],0)
    def test_public_seed_rejects_inconsistent_or_below_threshold_balances(self):
        from app.seed import public_holder_seed, GNK_HOLDER_SOURCE
        NativeHolders(SimpleNamespace(db=self.db,net=None))
        self.db.put('public_holders:GNK',dict(height=20,ts=10,checked_at=11,scanned=2,
            supply_raw=str(MINIMUM_RAW+7),source=GNK_HOLDER_SOURCE))
        self.db.conn.execute('INSERT INTO public_gnk_holders VALUES(?,?)',(G,str(MINIMUM_RAW)))
        self.db.conn.commit()
        self.assertEqual(len(public_holder_seed(self.db.conn)[1]),1)
        for raw in [MINIMUM_RAW-1,MINIMUM_RAW+100]:
            self.db.conn.execute('UPDATE public_gnk_holders SET balance_raw=?',(str(raw),))
            with self.assertRaises(ValueError):public_holder_seed(self.db.conn)

    def test_api_validation_and_no_zero_for_unknown(self):
        cfg=Settings(mode='mints',data_dir=Path(self.temp.name)/'api',indexer_enabled=False)
        with TestClient(create_app(cfg)) as client:
            result=client.get('/api/mints/holders').json()
            self.assertEqual(result['minimum'],10000);self.assertIsNone(result['total'])
            for query in ['asset=FAKE','limit=0','offset=-1','q=%3Cscript%3E']:
                self.assertEqual(client.get('/api/mints/holders?'+query).status_code,422)

if __name__=='__main__':unittest.main()
