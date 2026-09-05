import asyncio
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from fastapi.testclient import TestClient

from app.codec import kh
from app.config import Settings, TOKEN, ESCROW
from app.db import Database
from app.mints import MintIndexer, initialize, save_batch
from app.provenance import (verify_link, save_link, overview, indexed_incoming, save_page,
                            sync_state, incoming_history, coins)

A = 'gonka1gcrt8wraadkw5nmn03ggqd7qrt4rcy02zp4a6x'
B = 'gonka1fg0cq9hvcx0dejnp7q38zfmp00q87g2xw2xxtx'
C = 'gonka15wng2302rhq5w8ddy3l3jslrhfcpufzfs6wc3zc6cxt8cpwrfp4qqgenkc'
ETH = '0x'+'2'*40
RAW = '15000000000000'

def event(kind, **fields):
    return {'type':kind,'attributes':[{'key':k,'value':v} for k,v in fields.items()]}

def fixture():
    request = 'req_25_public_test_transaction'
    mint = {'tx_hash':'0x'+'1'*64,'log_index':2,'height':100,'block_hash':'0x'+'4'*64,
            'ts':1788632219,'recipient':ETH,'amount_raw':RAW,'request_id':kh(request),
            'epoch_id':'384','finalized':1,'source':'ethereum_rpc'}
    signing = {'created_block_height':'25','request_id':base64.b64encode(bytes.fromhex(kh(request)[2:])).decode(),
        'current_epoch_id':'384','status':'THRESHOLD_SIGNING_STATUS_COMPLETED',
        'data':[base64.b64encode(v).decode() for v in [(1).to_bytes(32,'big'),bytes(32),
                bytes.fromhex(ETH[2:]),bytes.fromhex(TOKEN[2:]),int(RAW).to_bytes(32,'big')]]}
    block = {'result':{'block_id':{'hash':'F'*64},'block':{'header':{'height':'25','chain_id':'gonka-mainnet',
        'time':'2026-09-05T18:09:46Z'},'data':{'txs':[base64.b64encode(b'public_test_tx').decode()]}}}}
    events = [event('transfer',sender=A,recipient=ESCROW,amount=RAW+'ngonka',msg_index='0'),
        event('bridge_mint_requested',user=A,amount=RAW,destination_address=ETH,destination_bridge_address=TOKEN,
              chain_id='ethereum',request_id=request,epoch_index='384',msg_index='0')]
    results = {'result':{'height':'25','txs_results':[{'code':0,'events':events}]}}
    return mint,{'signing_request':signing},block,results

def tx(h=100,quantity='1234567890',src=B,receiver=A):
    return {'tx_hash':format(h,'064x'),'block_height':h,'tx_index':0,
        'block_time':'2026-09-05 18:39:05.000','success':True,'tx_type':'transfer',
        'sender':src,'recipient':receiver,'amount':'999999999999ngonka',
        'events':json.dumps([event('coin_received',receiver=receiver,amount=quantity+'ngonka',msg_index='0'),
            event('transfer',sender=src,recipient=receiver,amount=quantity+'ngonka',msg_index='0')])}

def page(rows,offset=0,more=False):
    return {'address':A,'count':len(rows),'offset':offset,'limit':100,'has_more':more,'txs':rows}

class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.db=Database(':memory:');initialize(self.db)
    def tearDown(self):self.db.close()

    def test_bridge_link_matches_request_epoch_destination_amount_and_escrow(self):
        args=fixture();link=verify_link(*args)
        args[1]['signing_request']['status']=3
        self.assertEqual(verify_link(*args)['gnk_tx_hash'],link['gnk_tx_hash'])
        self.assertEqual(link['gnk_address'],A)
        self.assertEqual(link['gnk_tx_hash'],hashlib.sha256(b'public_test_tx').hexdigest().upper())
        save_batch(self.db,100,100,[args[0]],[])
        save_link(self.db,link);save_link(self.db,link)
        out=overview(self.db,ETH)
        self.assertEqual((out['verified'],out['total'],len(out['sources'])),(1,1,1))
        self.assertEqual(out['mining_attribution'],'not_inferred')
        with self.assertRaises(ValueError):save_link(self.db,{**link,'gnk_address':B})

    def test_bridge_rejects_wrong_chain_failed_missing_and_ambiguous_events(self):
        variants=[]
        args=fixture();args[0]['finalized']=0;variants.append(args)
        args=fixture();args[1]['signing_request']['current_epoch_id']='383';variants.append(args)
        args=fixture();args[2]['result']['block']['header']['chain_id']='wrong';variants.append(args)
        args=fixture();args[3]['result']['txs_results'][0]['code']=1;variants.append(args)
        args=fixture();args[3]['result']['txs_results'][0]['events'].pop(0);variants.append(args)
        args=fixture();args[3]['result']['txs_results'][0]['events'].append(args[3]['result']['txs_results'][0]['events'][-1]);variants.append(args)
        args=fixture();args[1]['signing_request']['request_id']=base64.b64encode(bytes(32)).decode();variants.append(args)
        args=fixture();args[1]['signing_request']['data'][-1]=base64.b64encode((1).to_bytes(32,'big')).decode();variants.append(args)
        for args in variants:
            with self.assertRaises(ValueError):verify_link(*args)

    def test_incoming_events_not_summary_exact_and_no_double_count(self):
        value=str(2**100+987654321)
        rows=indexed_incoming(tx(quantity=value),A)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['amount_raw'],value)
        self.assertEqual(rows[0]['src'],B);self.assertEqual(rows[0]['event_index'],1)
        self.assertEqual(rows[0]['ts'],1788633545)
        self.assertEqual(indexed_incoming(tx(receiver=B),A),[])
        self.assertEqual(indexed_incoming({**tx(),'success':False},A),[])
        self.assertEqual(indexed_incoming(tx(src=C),A)[0]['src'],C)
        self.assertEqual(coins('1ngonka,20ibc/AA'),1)
        with self.assertRaises(ValueError):coins('1.5ngonka')

    def test_multiple_real_transfers_and_unknown_sender_are_preserved(self):
        item=tx();events=json.loads(item['events'])
        events+=copy.deepcopy(events);item['events']=events
        self.assertEqual(len(indexed_incoming(item,A)),2)
        item['events']=[event('coin_received',receiver=A,amount=RAW+'ngonka')]
        result=indexed_incoming(item,A)
        self.assertEqual((result[0]['src'],result[0]['kind']),('','received_unknown'))

    def test_module_reward_is_not_inferred_from_plain_transfer_or_vesting_credit(self):
        item=tx();self.assertEqual(indexed_incoming(item,A)[0]['kind'],'transfer')
        events=json.loads(item['events'])
        events.append(event('message',action='/inference.inference.MsgClaimRewards',msg_index='0'))
        events.append(event('vest_reward',participant=A,amount='555ngonka'))
        item['events']=events
        self.assertEqual(len(indexed_incoming(item,A,{B:'inference'})),1)
        self.assertEqual(indexed_incoming(item,A,{B:'inference'})[0]['kind'],'reward_paid')
        self.assertEqual(indexed_incoming(item,A,{B:'streamvesting'})[0]['kind'],'vesting_unlock')

    def test_cached_history_pagination_overlap_live_update_and_restart(self):
        save_page(self.db,A,page([tx(h) for h in range(200,100,-1)],more=True))
        self.assertEqual(sync_state(self.db,A)['offset'],90)
        self.assertFalse(sync_state(self.db,A)['exhausted'])
        save_page(self.db,A,page([tx(h) for h in range(110,95,-1)],offset=90))
        data=incoming_history(self.db,A)
        self.assertEqual(data['total'],105)
        self.assertTrue(data['history']['exhausted'])
        self.assertFalse(data['full_chain_verified'])
        save_page(self.db,A,page([tx(201),tx(200)],more=True))
        data=incoming_history(self.db,A)
        self.assertEqual(data['total'],106)
        self.assertEqual(data['history']['offset'],0)
        self.assertEqual(data['history']['head'],format(201,'064x'))
        self.assertEqual(incoming_history(self.db,A,sort='time_asc')['total'],106)

    def test_missing_page_boundary_restarts_pass_and_preserves_rows(self):
        save_page(self.db,A,page([tx(h) for h in range(200,100,-1)],more=True))
        save_page(self.db,A,page([tx(300)],offset=90))
        self.assertEqual(sync_state(self.db,A)['offset'],0)
        self.assertFalse(sync_state(self.db,A)['exhausted'])
        self.assertEqual(incoming_history(self.db,A)['total'],100)

    def test_failed_page_is_atomic_does_not_advance_or_mean_zero(self):
        save_page(self.db,A,page([tx(2)],more=False))
        before=sync_state(self.db,A)
        with self.assertRaises(ValueError):save_page(self.db,A,{**page([tx(3)]),'count':2})
        self.assertEqual(sync_state(self.db,A),before)
        with self.assertRaises(ValueError):save_page(self.db,A,page([tx(3),tx(2,quantity='42')]))
        self.assertEqual(incoming_history(self.db,A)['total'],1)
        self.assertEqual(sync_state(self.db,A),before)

    def test_explorer_can_return_same_block_transactions_in_either_order(self):
        a,b=tx(2),tx(3)
        a['block_height']=b['block_height']=10;a['tx_index']=1;b['tx_index']=9
        save_page(self.db,A,page([a,b]))
        self.assertEqual(incoming_history(self.db,A)['total'],2)

    def test_seed_copies_only_linked_public_incoming_and_cursor(self):
        from app.seed import export_provenance
        args=fixture();save_batch(self.db,100,100,[args[0]],[]);save_link(self.db,verify_link(*args))
        save_page(self.db,A,page([tx(h) for h in range(200,100,-1)],more=True))
        # Foreign/private data in the working DB must not leak through a JSON copy.
        value=json.loads(self.db.conn.execute('SELECT value FROM gonka_incoming LIMIT 1').fetchone()[0])
        value['private_note']='DO_NOT_PUBLISH'
        self.db.conn.execute('UPDATE gonka_incoming SET value=? WHERE tx_hash=?',(json.dumps(value),value['tx_hash']))
        self.db.conn.execute('INSERT INTO gonka_address_sync VALUES(?,?)',(B,json.dumps({'private_note':'DO_NOT_PUBLISH'})))
        self.db.conn.commit()
        target=Database(':memory:');initialize(target)
        try:
            save_batch(target,100,100,[args[0]],[])
            export_provenance(self.db.conn,target)
            self.assertEqual(overview(target)['verified'],1)
            self.assertEqual(incoming_history(target,A)['total'],100)
            self.assertEqual(sync_state(target,A)['offset'],90)
            self.assertEqual(target.conn.execute('SELECT COUNT(*) FROM gonka_address_sync').fetchone()[0],1)
            for r in target.conn.execute('SELECT value FROM gonka_incoming'):
                self.assertNotIn('DO_NOT_PUBLISH',r[0])
        finally:target.close()

    def test_persistent_database_resumes_page_after_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3';db=Database(path);initialize(db)
            save_page(db,A,page([tx(h) for h in range(200,100,-1)],more=True));db.close()
            db=Database(path);initialize(db)
            try:
                self.assertEqual(sync_state(db,A)['offset'],90)
                save_page(db,A,page([tx(h) for h in range(110,95,-1)],offset=90))
                self.assertEqual(incoming_history(db,A)['total'],105)
            finally:db.close()

    def test_bundled_address_cache_is_available_before_any_network_search(self):
        from app.main import create_app
        from app.seed import DEFAULT_ARCHIVE
        manifest=json.loads((DEFAULT_ARCHIVE.parent/'manifest.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(mode='mints',history_from='',data_dir=Path(tmp),indexer_enabled=False,seed_enabled=True)
            with TestClient(create_app(cfg)) as client:
                db=client.app.state.db
                self.assertEqual(client.app.state.indexer.tasks,[])
                result=client.get('/api/mints/provenance').json()
                self.assertEqual(result['verified'],manifest['gonka_links'])
                self.assertGreater(result['verified'],0)
                total=0
                for source in result['sources']:
                    data=client.get('/api/mints/gonka/'+source['address']).json()
                    total+=data['total']
                    self.assertFalse(data['full_chain_verified'])
                    self.assertEqual(data['amount_raw'],str(sum(int(e['amount_raw']) for e in data['items'])))
                    self.assertEqual(data['history']['next_check'],0)
                self.assertEqual(total,manifest['gonka_incoming'])
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM events WHERE chain!='ethereum'").fetchone()[0],0)
                before=db.conn.execute('SELECT COUNT(*) FROM gonka_incoming').fetchone()[0]
            # A second application start must keep its existing cache.
            with TestClient(create_app(cfg)) as client:
                self.assertEqual(client.app.state.db.conn.execute('SELECT COUNT(*) FROM gonka_incoming').fetchone()[0],before)

    def test_api_only_reads_linked_addresses_without_starting_search(self):
        from app.main import create_app
        with tempfile.TemporaryDirectory() as tmp:
            with TestClient(create_app(Settings(mode='mints',history_from='',data_dir=Path(tmp),indexer_enabled=False))) as client:
                db=client.app.state.db;args=fixture();save_batch(db,100,100,[args[0]],[])
                self.assertEqual(client.get('/api/mints/provenance?address='+ETH).json()['pending'],1)
                self.assertEqual(client.get('/api/mints/gonka/'+A).status_code,404)
                save_link(db,verify_link(*args));save_page(db,A,page([tx()]))
                data=client.get('/api/mints/gonka/'+A).json()
                self.assertEqual(data['total'],1);self.assertFalse(data['full_chain_verified'])
                self.assertEqual(client.get('/api/mints/provenance?address='+ETH).json()['sources'][0]['address'],A)
                self.assertEqual(client.get('/api/mints/gonka/'+B).status_code,404)
                self.assertEqual(client.get('/api/mints/gonka/invalid').status_code,400)
                self.assertEqual(client.get('/api/mints/gonka/'+A+'?sort=bad').status_code,422)
                self.assertEqual(client.post('/api/mints/gonka/'+A).status_code,405)
                self.assertEqual(client.get('/api/holders').status_code,410)
                self.assertEqual(client.app.state.indexer.tasks,[])


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');self.owner=MintIndexer(Settings(mode='mints',history_from=''),self.db)
    async def asyncTearDown(self):await self.owner.stop();self.db.close()

    async def test_incoming_does_not_query_any_unlinked_address(self):
        self.owner.provenance.request=AsyncMock(side_effect=AssertionError('no network'))
        await self.owner.provenance.incoming()
        self.owner.provenance.request.assert_not_called()

    async def test_native_rpc_requests_only_selected_sparse_heights_and_caches(self):
        collector=self.owner.provenance
        async def reply(path,params=None,payload=None):
            self.assertEqual(path,'/chain-rpc/')
            self.assertEqual({p['params']['height'] for p in payload},{'25','5000000'})
            self.assertEqual(len(payload),4)
            rows=[]
            for p in payload:
                h=p['params']['height'];args=fixture()
                row=copy.deepcopy(args[2] if p['method']=='block' else args[3]);row['id']=p['id']
                if p['method']=='block':row['result']['block']['header']['height']=h
                else:row['result']['height']=h
                rows.append(row)
            return rows
        collector.request=AsyncMock(side_effect=reply)
        await collector.native_blocks([25,5000000])
        await collector.native_blocks([5000000,25])
        self.assertEqual(set(collector.blocks),{25,5000000})
        collector.request.assert_awaited_once()

    async def test_pruned_gateway_falls_back_only_for_requested_block(self):
        collector=self.owner.provenance
        collector.request=AsyncMock(return_value=[{'id':'block:25','error':{'message':'lowest height is 5000000'}}])
        self.owner.cfg.gonka_archive=['https://archive.example/chain-rpc']
        args=fixture();rows=[{**args[2],'id':'block:25'},{**args[3],'id':'block_results:25'}]
        response=Mock();response.json.return_value=rows
        collector.net.client.post=AsyncMock(return_value=response)
        await collector.native_blocks([25])
        self.assertEqual(set(collector.blocks),{25})
        self.assertEqual(collector.gateway_floor,5000000)
        request=collector.net.client.post.call_args
        self.assertEqual(request.args[0],'https://archive.example/chain-rpc/')
        self.assertEqual([p['params']['height'] for p in request.kwargs['json']],['25','25'])

    async def test_incomplete_native_response_never_caches_partial_block(self):
        collector=self.owner.provenance
        collector.request=AsyncMock(return_value=[{**fixture()[2],'id':'block:25'}])
        self.owner.cfg.gonka_archive=[]
        with self.assertRaises(ValueError):await collector.native_blocks([25])
        self.assertEqual(collector.blocks,{})

    async def test_failed_link_is_retried_and_does_not_block_other_mints(self):
        args=fixture();save_batch(self.db,100,100,[args[0]],[])
        collector=self.owner.provenance
        collector.request=AsyncMock(return_value=args[1]);collector.native_blocks=AsyncMock()
        collector.resolve=AsyncMock(side_effect=ValueError('unavailable'))
        await collector.links()
        self.assertEqual(overview(self.db)['verified'],0)
        self.assertEqual(self.db.conn.execute('SELECT attempts FROM gonka_link_attempts').fetchone()[0],1)
        await collector.links();self.assertEqual(collector.resolve.await_count,1)

    async def test_incoming_error_retains_old_rows_cursor_and_explicit_error(self):
        args=fixture();save_batch(self.db,100,100,[args[0]],[]);save_link(self.db,verify_link(*args))
        save_page(self.db,A,page([tx(h) for h in range(200,100,-1)],more=True))
        collector=self.owner.provenance;collector.modules={}
        collector.request=AsyncMock(side_effect=ValueError('unavailable'))
        await collector.incoming()
        data=incoming_history(self.db,A)
        self.assertEqual(data['total'],100);self.assertEqual(data['history']['offset'],90)
        self.assertIn('unavailable',data['history']['error'])
