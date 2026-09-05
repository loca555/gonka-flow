import base64
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings, TOKEN, USDT, ESCROW, ZERO
from app.codec import (kh, TRANSFER, SWAP, MINT, BURN, LP_MINT, LP_BURN, blank,
                       parse_native, parse_eth_log, signed, pb, amount)
from app.db import Database,tokens
from app.analytics import bridges,rankings,overview
from app.main import create_app

B={"height":100,"hash":"0x"+"1"*64,"ts":int(time.time())}
USER="0x"+"2"*40
OTHER="0x"+"3"*40
POOL="0x"+"4"*40
GNK="gonka1s78erp5ynf49pljpmjs4apz9mq8ecpaul0eca0"
MODULE="gonka1jftn8khawsmfn7shgzfjn27myu5d4zd6ns09y8"
POOLS={POOL:{"token0":TOKEN,"token1":USDT,"quote_decimals":6,"quote_symbol":"USDT"}}

def word(n):
    return hex(n%(2**256))[2:].zfill(64)

def topic_addr(a):
    return "0x"+a[2:].zfill(64)

def ev(kind,**values):
    return {"type":kind,"attributes":[{"key":k,"value":str(v)} for k,v in values.items()]}

def native(events,code=0,system=None):
    block={"result":{"block_id":{"hash":"A"*64},"block":{"header":{
        "height":"100","time":"2026-09-05T00:00:00Z","chain_id":"gonka-mainnet"},
        "data":{"txs":[base64.b64encode(b"\x0a\x00").decode()]}}}}
    result={"result":{"height":"100","txs_results":[{"code":code,"events":events}],
                      "finalize_block_events":system or []}}
    return block,result

def ethlog(topic,values,topics,contract=TOKEN):
    return {"address":contract,"topics":[topic]+topics,"data":"0x"+"".join(word(v) for v in values),
            "blockNumber":"0x64","blockHash":B["hash"],"transactionHash":"0x"+"5"*64,
            "transactionIndex":"0x2","logIndex":"0x3"}

class CodecTests(unittest.TestCase):
    def test_keccak_not_sha3(self):
        self.assertEqual(TRANSFER,"0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
    def test_precision_above_256_bit(self):
        n=2**256-1
        s=tokens(n)
        self.assertEqual(int(s.replace(".","")),n)
        self.assertEqual(tokens(-1),"-0.000000001")
    def test_amount_and_zero(self):
        self.assertEqual(amount("12ngonka"),12)
        self.assertEqual(amount("7other,12345678901234567890ngonka"),12345678901234567890)
        self.assertEqual(amount(""),0)
    def test_failed_native_has_no_money(self):
        b,r=native([ev("transfer",sender=GNK,recipient=ESCROW,amount="4ngonka")],code=5)
        self.assertEqual(parse_native(b,r,{})[1],[])
    def test_no_reward_from_empty_claim(self):
        b,r=native([ev("message",action="/inference.inference.MsgClaimRewards")])
        self.assertEqual(parse_native(b,r,{})[1],[])
    def test_reward_requires_transfer(self):
        b,r=native([ev("message",action="/inference.inference.MsgClaimRewards"),
                    ev("transfer",sender=MODULE,recipient=GNK,amount="9000000000ngonka")])
        self.assertEqual(parse_native(b,r,{MODULE:"inference"})[1][0]["kind"],"reward_paid")
    def test_unrelated_message_not_a_reward(self):
        b,r=native([ev("message",action="/inference.inference.MsgClaimRewards",msg_index="0"),
                    ev("message",action="/other.Refund",msg_index="1"),
                    ev("transfer",sender=MODULE,recipient=GNK,amount="9000000000ngonka",msg_index="1")])
        self.assertEqual(parse_native(b,r,{MODULE:"inference"})[1][0]["kind"],"module_transfer")
    def test_bridge_not_double_counted(self):
        b,r=native([ev("transfer",sender=GNK,recipient=ESCROW,amount="1000000000ngonka",msg_index="0"),
          ev("bridge_mint_requested",user=GNK,amount="1000000000",destination_address=USER,
             destination_bridge_address=TOKEN,chain_id="ethereum",request_id="req_test",msg_index="0")])
        result=parse_native(b,r,{ESCROW:"bridge_escrow"})[1]
        self.assertEqual(len(result),1);self.assertEqual(result[0]["kind"],"bridge_lock")
        self.assertEqual(result[0]["request_key"],kh("req_test"))
    def test_bogus_bridge_contract_not_a_bridge(self):
        b,r=native([ev("transfer",sender=GNK,recipient=ESCROW,amount="100ngonka"),
          ev("bridge_mint_requested",user=GNK,amount="100",destination_address=USER,
             destination_bridge_address=OTHER,chain_id="ethereum",request_id="req_test")])
        self.assertEqual(parse_native(b,r,{})[1][0]["kind"],"escrow_deposit")
    def test_system_vesting_included(self):
        b,r=native([],system=[ev("transfer",sender=MODULE,recipient=GNK,amount="100ngonka")])
        self.assertEqual(parse_native(b,r,{MODULE:"streamvesting"})[1][0]["kind"],"vesting_unlock")
    def test_plain_vesting_not_mining(self):
        b,r=native([ev("vest_reward",participant=GNK,amount="100ngonka")])
        self.assertEqual(parse_native(b,r,{})[1][0]["kind"],"vesting_credit")
    def test_malformed_block_fails(self):
        b,r=native([]);r["result"]["height"]="101"
        with self.assertRaises(ValueError):parse_native(b,r,{})
    def test_protobuf_truncation_fails(self):
        with self.assertRaises(ValueError):pb(b"\x0a\x0f")
    def test_swap_sign_both_orientations(self):
        for reversed_tokens in (False,True):
            pools=copy.deepcopy(POOLS)
            if reversed_tokens:pools[POOL]["token0"],pools[POOL]["token1"]=USDT,TOKEN
            for qty,expected in [(10**9,"sell"),(-10**9,"buy")]:
                values=[qty,-1000000 if qty>0 else 1000000]
                if reversed_tokens:values.reverse()
                log=ethlog(SWAP,values+[0,0,0],[topic_addr(OTHER),topic_addr(USER)],POOL)
                result=parse_eth_log(log,B,pools)
                self.assertEqual(result["kind"],expected)
                self.assertEqual(result["amount_raw"],str(10**9))
    def test_router_is_not_claimed_economic_actor(self):
        log=ethlog(SWAP,[10**9,-100000,0,0,0],[topic_addr(OTHER),topic_addr(USER)],POOL)
        receipt={"from":USER,"logs":[]}
        result=parse_eth_log(log,B,POOLS,receipt)
        self.assertEqual(result["meta"]["attribution"],"initiator_only")
    def test_buyer_net_verified(self):
        log=ethlog(SWAP,[-10**9,100000,0,0,0],[topic_addr(OTHER),topic_addr(USER)],POOL)
        transfer=ethlog(TRANSFER,[10**9],[topic_addr(POOL),topic_addr(USER)])
        result=parse_eth_log(log,B,POOLS,{"from":USER,"logs":[transfer]})
        self.assertEqual(result["meta"]["attribution"],"initiator_net")
    def test_lp_not_swap(self):
        log=ethlog(LP_MINT,[int(OTHER,16),1,10**9,10**6],[topic_addr(USER),word(1),word(2)],POOL)
        self.assertEqual(parse_eth_log(log,B,POOLS)["kind"],"liquidity_add")
    def test_mint_transfer_not_double_counted(self):
        log=ethlog(TRANSFER,[10**9],[topic_addr(ZERO),topic_addr(USER)])
        self.assertIsNone(parse_eth_log(log,B,{}))
        log=ethlog(MINT,[10**9],["0x"+word(383),kh("request"),topic_addr(USER)])
        self.assertEqual(parse_eth_log(log,B,{})["kind"],"bridge_mint")
    def test_removed_logs_ignored(self):
        log=ethlog(BURN,[123,1],[topic_addr(USER)]);log["removed"]=True
        self.assertIsNone(parse_eth_log(log,B,{}))

class DBTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"test.sqlite3")
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_idempotence_and_coverage_holes(self):
        e=blank("ethereum",B,"tx",1,"buy",2**80)
        self.db.save_batch("ethereum",100,110,[e],[B])
        self.db.save_batch("ethereum",100,110,[e],[B])
        self.db.save_batch("ethereum",120,130,[],[])
        self.assertEqual(self.db.events()["total"],1)
        self.assertEqual(len(self.db.coverage("ethereum")["ranges"]),2)
        self.db.save_batch("ethereum",111,119,[],[])
        self.assertEqual(self.db.coverage("ethereum")["blocks"],31)
    def test_provisional_reorg(self):
        e=blank("ethereum",B,"orphan",1,"sell",5,finalized=0)
        self.db.save_batch("ethereum",100,110,[e],[],False,True)
        self.db.save_batch("ethereum",100,111,[],[],False,True)
        self.assertEqual(self.db.events()["total"],0)
        self.assertEqual(self.db.coverage("ethereum")["blocks"],0)
    def test_finalized_conflict_rollback(self):
        e=blank("ethereum",B,"safe",1,"sell",5)
        self.db.save_batch("ethereum",100,100,[e],[B])
        with self.assertRaises(ValueError):
            self.db.save_batch("ethereum",100,100,[],[{**B,"hash":"different"}])
        self.assertEqual(self.db.events()["total"],1)
    def test_bigint_aggregate(self):
        events=[blank("ethereum",B,"tx",i,"buy",2**200) for i in range(3)]
        self.db.save_batch("ethereum",100,100,events,[B])
        value=self.db.conn.execute("SELECT sumint(amount_raw) FROM events").fetchone()[0]
        self.assertEqual(int(value),3*2**200)
    def test_exact_bridge_pairing(self):
        lock=blank("gonka",B,"native",1,"bridge_lock",10**9,GNK,ESCROW,
                   request_key=kh("request"),meta={"destination":USER})
        mint=blank("ethereum",B,"eth",1,"bridge_mint",10**9,ZERO,USER,request_key=kh("request"))
        self.db.save_batch("gonka",100,100,[lock],[B])
        self.db.save_batch("ethereum",100,100,[mint],[B])
        self.assertEqual(bridges(self.db,24)["items"][0]["status"],"completed")
        self.assertEqual(bridges(self.db,24)["total"],1)
    def test_mismatching_amount_not_paired(self):
        lock=blank("gonka",B,"native",1,"bridge_lock",1,GNK,ESCROW,
                   request_key=kh("request"),meta={"destination":USER})
        mint=blank("ethereum",B,"eth",1,"bridge_mint",2,ZERO,USER,request_key=kh("request"))
        self.db.save_batch("gonka",100,100,[lock],[B]);self.db.save_batch("ethereum",100,100,[mint],[B])
        self.assertNotEqual(bridges(self.db,24)["items"][0]["status"],"completed")
    def test_router_not_in_rankings(self):
        e=blank("ethereum",B,"tx",1,"sell",99,actor=USER,meta={"attribution":"initiator_only"})
        self.db.save_batch("ethereum",100,100,[e],[B])
        self.assertEqual(rankings(self.db,24,"sell")["items"],[])
        self.assertIn("разблокированного",rankings(self.db,24,"unlocks")["note"])
        self.assertIn("награды",rankings(self.db,24,"rewards")["note"])
    def test_api_empty_database_and_validation(self):
        cfg=Settings(data_dir=Path(self.temp.name)/"api",indexer_enabled=False)
        with TestClient(create_app(cfg)) as client:
            self.assertEqual(client.get("/healthz").status_code,200)
            self.assertEqual(client.get("/api/overview").json()["stored_events"],0)
            self.assertEqual(client.get("/api/hosts").json()["items"],[])
            self.assertEqual(client.get("/api/wallet/not-an-address").status_code,400)
            self.assertEqual(client.get("/api/events?minimum=-1").status_code,422)
            self.assertEqual(client.get("/api/events?limit=1000000").status_code,422)
            self.assertEqual(client.post("/api/events").status_code,405)
            self.assertIn("nosniff",client.get("/").headers["x-content-type-options"])

if __name__=="__main__":
    unittest.main()
