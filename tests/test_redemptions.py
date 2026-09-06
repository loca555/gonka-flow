import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from app.codec import blank, BURN
from app.config import TOKEN, ZERO, Settings
from app.db import Database
from app.mints import initialize, MintIndexer, save_batch
from app.provenance import overview, incoming_history, save_balance, save_link, verify_link
from app.redemptions import burn_index, verify_burn, save_burn_link, burn_overview, native_addresses
from test_provenance import A, B, ETH, fixture, tx, page


def fixture_burn(height=120, address=ETH, raw='123456789012345678901234567890'):
    block={'height':height,'hash':'0x'+format(height,'064x'),'ts':1788632219+height}
    burn=blank('ethereum',block,'0x'+format(height+1000,'064x'),2,'bridge_burn',raw,address,ZERO,
        request_key=f'ethereum:{height}:5',meta={'contract':TOKEN,'topic':BURN,'transaction_index':5})
    record={'chainId':'ethereum','contractAddress':TOKEN,'ownerAddress':B,'amount':raw,
        'status':'BRIDGE_COMPLETED','blockNumber':str(height),'receiptIndex':'5',
        'id':f'ethereum_{height}_public_test','epochIndex':'385'}
    return burn, {'bridgeTransactions':[record]}, block


def store(db, fixture):
    burn,_,block=fixture
    db.save_batch('ethereum',burn['height'],burn['height'],[burn],[block])


class BurnTests(unittest.TestCase):
    def setUp(self):
        self.db=Database(':memory:');initialize(self.db)
    def tearDown(self):self.db.close()

    def test_burn_only_address_is_archived_without_any_mint(self):
        burn,reply,block=fixture_burn();store(self.db,(burn,reply,block))
        before=overview(self.db,ETH)
        self.assertEqual(before['total'],0);self.assertEqual(before['burns']['total'],1)
        self.assertEqual(before['burns']['verified'],0);self.assertEqual(before['sources'],[])
        link=verify_burn(burn,reply);save_burn_link(self.db,link);save_burn_link(self.db,link)
        data=overview(self.db,ETH);source=data['sources'][0]
        self.assertEqual((data['total'],data['verified'],data['burns']['verified']),(0,0,1))
        self.assertEqual((source['mints'],source['burns'],source['address']),(0,1,B))
        self.assertEqual(source['burned_raw'],burn['amount_raw']);self.assertEqual(source['amount_raw'],'0')
        self.assertEqual(native_addresses(self.db),[B])
        self.assertEqual(data['burns']['links'][0]['verification'],'gonka_bridge_state')
        self.assertIsNone(link['gnk_tx_hash']);self.assertIsNone(link['gnk_ts'])
        self.assertIn('/120/5',link['evidence_url'])

    def test_one_address_can_mint_and_burn_without_mixing_amounts(self):
        mint=fixture();save_batch(self.db,100,100,[mint[0]],[]);save_link(self.db,verify_link(*mint))
        args=fixture_burn();args[1]['bridgeTransactions'][0]['ownerAddress']=A;store(self.db,args)
        save_burn_link(self.db,verify_burn(*args[:2]))
        data=overview(self.db,ETH);source=data['sources'][0]
        self.assertEqual(len(data['sources']),1)
        self.assertEqual((source['mints'],source['burns']),(1,1))
        self.assertEqual(source['amount_raw'],mint[0]['amount_raw'])
        self.assertEqual(source['burned_raw'],args[0]['amount_raw'])
        self.assertEqual(overview(self.db,ETH,through=110)['burns']['total'],0)
        self.assertEqual(overview(self.db,'0x'+'9'*40)['sources'],[])

    def test_pending_wrong_amount_network_contract_coordinates_or_owner_not_confirmed(self):
        burn,reply,_=fixture_burn()
        for field,value in [('status','BRIDGE_PENDING'),('amount','1'),('chainId','wrong'),
                ('contractAddress',ZERO),('blockNumber','121'),('receiptIndex','6'),
                ('ownerAddress','invalid'),('epochIndex','-1'),('id','')]:
            with self.subTest(field=field):
                altered=copy.deepcopy(reply);altered['bridgeTransactions'][0][field]=value
                with self.assertRaises(ValueError):verify_burn(burn,altered)
        for reply in ({},{'bridgeTransactions':[]},{'bridgeTransactions':[reply['bridgeTransactions'][0]]*2}):
            with self.assertRaises(ValueError):verify_burn(burn,reply)

    def test_unfinalized_or_malformed_burn_rejected_and_seed_coordinates_supported(self):
        burn,reply,_=fixture_burn()
        for field,value in [('finalized',0),('kind','transfer'),('src',ZERO),('amount_raw','-1'),
                            ('request_key','ethereum:121:5'),('idx',-1)]:
            with self.subTest(field=field),self.assertRaises(ValueError):verify_burn({**burn,field:value},reply)
        self.assertEqual(burn_index({**burn,'meta':{'contract':TOKEN,'topic':BURN}}),5)
        with self.assertRaises(ValueError):burn_index({**burn,'meta':{**burn['meta'],'transaction_index':6}})

    def test_multiple_burns_in_one_receipt_never_guessed(self):
        burn,reply,block=fixture_burn();second={**burn,'id':burn['id']+'2','idx':3}
        self.db.save_batch('ethereum',120,120,[burn,second],[block])
        with self.assertRaises(ValueError):save_burn_link(self.db,verify_burn(burn,reply))
        self.assertEqual(burn_overview(self.db)['verified'],0)

    def test_conflicting_link_is_rejected_and_finality_scopes_native_addresses(self):
        args=fixture_burn();store(self.db,args);link=verify_burn(*args[:2]);save_burn_link(self.db,link)
        with self.assertRaises(ValueError):save_burn_link(self.db,{**link,'gnk_address':A})
        with self.assertRaises(ValueError):save_burn_link(self.db,{**link,'amount_raw':'1'})
        self.db.conn.execute('UPDATE events SET finalized=0')
        self.assertEqual(native_addresses(self.db),[]);self.assertEqual(burn_overview(self.db)['total'],0)

    def test_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3';db=Database(path);initialize(db);args=fixture_burn()
            store(db,args);save_burn_link(db,verify_burn(*args[:2]));save_balance(db,B,{'denom':'ngonka','amount':'0'},999);db.close()
            db=Database(path);initialize(db)
            try:
                self.assertEqual(burn_overview(db)['verified'],1)
                self.assertEqual(incoming_history(db,B)['address_balance']['amount_raw'],'0')
            finally:db.close()

    def test_api_reads_burn_only_address_without_triggering_collection(self):
        from app.main import create_app
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(mode='mints',data_dir=Path(tmp),indexer_enabled=False,seed_enabled=False)
            with TestClient(create_app(cfg)) as client:
                db=client.app.state.db;args=fixture_burn();store(db,args)
                self.assertEqual(client.get('/api/mints/gonka/'+B).status_code,404)
                save_burn_link(db,verify_burn(*args[:2]))
                self.assertEqual(client.get('/api/mints/gonka/'+B).status_code,200)
                data=client.get('/api/mints/provenance?address='+ETH.upper().replace('0X','0x')).json()
                self.assertEqual(data['burns']['verified'],1)
                self.assertEqual(client.get('/api/mints/gonka/'+A).status_code,404)
                self.assertEqual(client.post('/api/mints/gonka/'+B).status_code,405)
                self.assertEqual(client.app.state.indexer.tasks,[])

    def test_export_preserves_only_burn_linked_included_addresses(self):
        from app.seed import export_provenance
        from app.provenance import save_page
        args=fixture_burn();store(self.db,args);save_burn_link(self.db,verify_burn(*args[:2]))
        save_page(self.db,B,{**page([tx(receiver=B,src=A)]),'address':B})
        save_balance(self.db,B,{'denom':'ngonka','amount':'12'},999)
        save_page(self.db,A,page([tx()]))
        target=Database(':memory:');initialize(target);store(target,args)
        try:
            export_provenance(self.db.conn,target)
            self.assertEqual(native_addresses(target),[B])
            self.assertEqual(burn_overview(target)['verified'],1)
            self.assertEqual(incoming_history(target,B)['total'],1)
            self.assertEqual(incoming_history(target,B)['address_balance']['amount_raw'],'12')
            self.assertEqual(incoming_history(target,A)['total'],0)
        finally:target.close()


class BurnCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');self.owner=MintIndexer(Settings(mode='mints'),self.db)
        self.collector=self.owner.provenance
    async def asyncTearDown(self):await self.owner.stop();self.db.close()

    async def test_only_archived_burns_are_queried_once_and_reused_for_balances(self):
        self.collector.request=AsyncMock(side_effect=AssertionError('no network'))
        await self.collector.burns();self.collector.request.assert_not_called()
        args=fixture_burn();store(self.db,args)
        self.collector.balance_chain_checked=int(time.time())
        self.collector.request=AsyncMock(return_value=args[1]);await self.collector.burns()
        self.collector.request.assert_awaited_once_with('/chain-api/productscience/inference/inference/bridge_transaction/ethereum/120/5')
        await self.collector.burns();self.assertEqual(self.collector.request.await_count,1)
        self.collector.request=AsyncMock(return_value={'data':{'balance':{'denom':'ngonka','amount':'500'}},'height':999})
        await self.collector.balances()
        self.assertEqual(self.collector.request.call_args.args[0],'/chain-api/cosmos/bank/v1beta1/balances/'+B+'/by_denom')
        self.assertEqual(incoming_history(self.db,B)['address_balance']['amount_raw'],'500')

    async def test_failed_lookup_does_not_block_others_or_reset_saved_links(self):
        a,b=fixture_burn(120),fixture_burn(121);store(self.db,a);store(self.db,b)
        self.collector.balance_chain_checked=int(time.time())
        self.collector.request=AsyncMock(side_effect=[ValueError('source outage'),a[1]])
        await self.collector.burns();data=burn_overview(self.db)
        self.assertEqual((data['total'],data['verified'],data['pending']),(2,1,1))
        self.assertEqual(data['items'][0]['error'],'source outage')
        await self.collector.burns();self.assertEqual(self.collector.request.await_count,2)

    async def test_burn_only_recipient_gets_targeted_incoming_history(self):
        args=fixture_burn();store(self.db,args);save_burn_link(self.db,verify_burn(*args[:2]))
        self.collector.modules={};self.db.put('provenance:index_head',{'height':999,'checked_at':int(time.time())})
        self.collector.request=AsyncMock(return_value={**page([tx(receiver=B,src=A)]),'address':B})
        await self.collector.incoming()
        self.collector.request.assert_awaited_once_with('/api/ch/address/'+B,{'limit':100,'offset':0})
        self.assertEqual(incoming_history(self.db,B)['total'],1)

    async def test_wrong_native_network_never_admits_a_recipient(self):
        args=fixture_burn();store(self.db,args)
        self.collector.request=AsyncMock(return_value={'result':{'node_info':{'network':'wrong'},'sync_info':{'catching_up':False}}})
        with self.assertRaises(ValueError):await self.collector.burns()
        self.assertEqual(native_addresses(self.db),[]);self.collector.request.assert_awaited_once()
