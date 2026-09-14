import unittest
from app.codec import blank
from app.config import ZERO
from app.holder_classification import estimate

G,H,M=('gonka1'+c*38 for c in 'qpz')
A,B,P=('0x'+c*40 for c in '123')
def event(chain,kind,q,src='',dst='',t=1,idx=0,tx=None,**kw):
    return blank(chain,dict(height=t,ts=t,hash='block'),tx or str(t),idx,kind,q,src,dst,**kw)
def mint(q=100,t=2,address=A,origin=G):
    e=event('ethereum','bridge_mint',q,ZERO,address,t)
    link=dict(tx_hash=e['tx_hash'],log_index=0,request_id='request',gnk_address=origin)
    return e,link

def sale(q=40,t=4,actor=B):
    return [event('ethereum','transfer',q,actor,P,t),event('ethereum','sell',q,t=t,idx=1,pool=P,actor=actor,meta=dict(attribution='initiator_net'))]

class ClassificationTests(unittest.TestCase):
    def run_model(self,eth,native=(),mints=(),burns=(),**kw):
        return estimate([G],native,eth,mints,burns,{P},**kw)[G]
    def test_sale_follows_another_ethereum_address(self):
        e,l=mint();r=self.run_model([e,event('ethereum','transfer',100,A,B,3),*sale()],mints=[l])
        self.assertEqual((r['category'],r['sold_raw']),('sellers','40'));self.assertTrue(r['estimated']);self.assertFalse(r['history_complete'])
    def test_mixed_balance_counts_whole_execution_exactly(self):
        huge=10**30+1;e,l=mint(huge)
        eth=[event('ethereum','bridge_mint',huge,ZERO,B,1),e,event('ethereum','transfer',huge,A,B,3),*sale(huge)]
        r=self.run_model(eth,mints=[l]);self.assertEqual(r['sold_raw'],str(huge))
    def test_native_intermediary_before_bridge(self):
        e,l=mint(t=3,origin=H)
        native=[event('gonka','transfer',100,G,H,1),event('gonka','bridge_lock',100,H,M,2,request_key='request')]
        r=self.run_model([e,*sale(100,4,A)],native,[l],native_balances={H:0});self.assertEqual(r['sold_raw'],'100')
    def test_native_snapshot_retained_balance_is_a_common_flow(self):
        e,l=mint(t=3,origin=H)
        native=[event('gonka','transfer',100,G,H,1),event('gonka','bridge_lock',100,H,M,2,request_key='request')]
        self.assertEqual(self.run_model([e,*sale(100,4,A)],native,[l],native_balances={H:100})['sold_raw'],'100')
    def test_verified_native_transfers_form_common_flow(self):
        e,l=mint(t=3,origin=H)
        native=[event('gonka','transfer',100,G,H,1),event('gonka','bridge_lock',100,H,M,2,request_key='request')]
        self.assertEqual(self.run_model([e,*sale(100,4,A)],native,[l])['sold_raw'],'100')
    def test_module_hub_does_not_spread_ancestry(self):
        e,l=mint(t=4,origin=H)
        native=[event('gonka','module_transfer',100,G,M,1),event('gonka','module_transfer',100,M,H,2),event('gonka','bridge_lock',100,H,M,3,request_key='request')]
        r=self.run_model([e,*sale(100,5,A)],native,[l],modules={M});self.assertEqual(r['sold_raw'],'0')
    def test_purchase_returned_over_verified_bridge(self):
        eth=[event('ethereum','bridge_mint',100,ZERO,P,1),event('ethereum','transfer',50,P,A,2),event('ethereum','buy',50,t=2,idx=1,pool=P,actor=A,meta=dict(attribution='initiator_net')),event('ethereum','bridge_burn',50,A,ZERO,3,request_key='return')]
        r=self.run_model(eth,burns=[dict(tx_hash='3',log_index=0,gnk_address=G)])
        self.assertEqual((r['category'],r['bought_raw']),('investors','50'))
    def test_mixing_does_not_inflate_actual_purchase_or_count_it_twice(self):
        eth=[event('ethereum','bridge_mint',100,ZERO,P,1),event('ethereum','bridge_mint',100,ZERO,A,1,idx=1),event('ethereum','transfer',1,P,A,2),event('ethereum','buy',1,t=2,idx=1,pool=P,actor=A,meta=dict(attribution='initiator_net')),event('ethereum','bridge_burn',50,A,ZERO,3,request_key='r1'),event('ethereum','bridge_burn',50,A,ZERO,4,request_key='r2')]
        burns=[dict(tx_hash=str(t),log_index=0,gnk_address=G) for t in (3,4)]
        r=self.run_model(eth,burns=burns)
        self.assertEqual(r['bought_raw'],'1')
    def test_liquidity_is_not_sale(self):
        e,l=mint();eth=[e,event('ethereum','transfer',100,A,P,3),event('ethereum','liquidity_add',100,t=3,idx=1,pool=P)]
        self.assertEqual(self.run_model(eth,mints=[l])['sold_raw'],'0')
    def test_miner_requires_reward_identity(self):
        reward=event('gonka','reward_vested',100,dst=G,meta=dict(mining_participant=G))
        self.assertEqual(self.run_model([], [reward])['category'],'miners')
        self.assertEqual(self.run_model([], [reward],native_complete=True)['category'],'miners')
        reward['meta']={}
        self.assertEqual(self.run_model([], [reward],native_complete=True)['category'],'unknown')
    def test_miner_transfers_are_not_sales(self):
        native=[event('gonka','reward_paid',100,M,G,meta=dict(mining_participant=G)),event('gonka','transfer',20,G,H,2)]
        self.assertEqual(self.run_model([],native,native_complete=True)['category'],'miners')
    def test_missing_ethereum_ledger_fails_instead_of_inventing_funding(self):
        with self.assertRaises(ValueError): self.run_model([event('ethereum','transfer',100,A,B)])

    def test_miner_sales_threshold_is_strict_and_has_priority(self):
        reward=event('gonka','reward_vested',100,dst=G,meta=dict(mining_participant=G))
        for sold,expected in [(0,'miners'),(9,'miners'),(10,'sellers'),(11,'sellers')]:
            e,l=mint()
            self.assertEqual(self.run_model([e,*sale(sold,4,A)] if sold else [e],[reward],[l])['category'],expected)
