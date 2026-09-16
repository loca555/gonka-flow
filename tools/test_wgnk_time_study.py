"""Small deterministic tests for local research; no DB or RPC access."""
import unittest
from datetime import datetime
from fractions import Fraction
import wgnk_time_study as s


def event(date, height, price, kind='buy', idx=0):
    stamp=datetime.fromisoformat(date).replace(tzinfo=s.MSK)
    return dict(ts=int(stamp.timestamp()),height=height,idx=idx,hour=stamp.hour,
        numerator=price*2**192,kind=kind,amount_raw='10000000000000000001',
        quote_raw='2000000',tx_hash='0x'+str(height),actor='alice',
        meta={'attribution':'initiator_net'})


class IntradayTests(unittest.TestCase):
    def test_timezone(self):
        self.assertEqual(s.iso(0),'1970-01-01T03:00:00+03:00')

    def test_twap_exact_hour_and_midnight(self):
        rows=[event('2026-06-09T20:00',1,2),event('2026-06-10T06:30',2,1,'sell'),
              event('2026-06-10T18:00',3,3),event('2026-06-11T00:00',4,4)]
        end=int(datetime(2026,6,11,tzinfo=s.MSK).timestamp())
        d=s.make_days(rows,end)[0]
        self.assertEqual(d['date'],'2026-06-10')
        self.assertEqual(d['twap'][6],1.5)
        self.assertEqual(d['twap'][17],1)
        self.assertEqual(d['twap'][18],3)
        self.assertEqual(d['low_hour'],6)
        self.assertEqual(d['high_hour'],18)
        self.assertEqual(d['low_actor'],'alice')
        self.assertEqual(d['events'],2)
        self.assertEqual(d['sell_raw'],'10000000000000000001')
        self.assertEqual(d['near_low'][6],.5)
        self.assertEqual(d['active_hour_list'],[6,18])

    def test_carried_open_not_a_new_high(self):
        rows=[event('2026-06-09T20:00',1,3),event('2026-06-10T06:00',2,2,'sell')]
        d=s.make_days(rows,int(datetime(2026,6,11,tzinfo=s.MSK).timestamp()))[0]
        self.assertTrue(d['high_carried'])
        self.assertEqual(d['high_hour'],0)
        self.assertEqual(d['high'],3)
        self.assertEqual(d['fresh_high_hour'],6)

    def test_last_swap_in_block_not_intrablock_wick(self):
        rows=[event('2026-06-09T20:00',1,2),event('2026-06-10T06:00',2,1,idx=9),
              event('2026-06-10T06:00',2,100,idx=3)]
        d=s.make_days(rows,int(datetime(2026,6,11,tzinfo=s.MSK).timestamp()))[0]
        self.assertEqual(d['high'],2)
        self.assertEqual(d['low'],1)

    def test_empty_day_carries_price_not_zero(self):
        rows=[event('2026-06-09T20:00',1,2)]
        d=s.make_days(rows,int(datetime(2026,6,11,tzinfo=s.MSK).timestamp()))[0]
        self.assertEqual(d['events'],0)
        self.assertEqual(d['twap'],[2]*24)
        self.assertTrue(d['low_carried'])
        self.assertIsNone(d['fresh_low_hour'])

    def test_price_decimals(self):
        sqrt=2**96
        self.assertEqual(Fraction(sqrt**2*1000,2**192),1000)


if __name__=='__main__':unittest.main()
