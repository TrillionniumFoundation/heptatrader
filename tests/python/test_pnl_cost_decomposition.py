"""Pure deterministic economics tests; no IB connection, data mutation or orders."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / "research"))
from pnl_cost_decomposition import attribution, summary


def fill(name, side, qty, price, commission):
    return {"command_id": name, "side": side, "quantity": qty, "price": price,
            "commission": commission}


class EpisodeTests(unittest.TestCase):
    def test_long_gross_positive_but_fee_negative(self):
        x=[fill("open", "BUY", "100", "1.10", "2"),
           fill("close", "SELL", "100", "1.11", "2")]
        eps,opened,book,missing=attribution(x,{"open":1000,"close":2000})
        self.assertFalse(opened)
        self.assertEqual(missing,0)
        self.assertEqual(eps[0]["gross_usd"],"1.00")
        self.assertEqual(eps[0]["commissions_usd"],"4")
        self.assertEqual(eps[0]["net_usd"],"-3.00")
        self.assertEqual(eps[0]["send_to_send_seconds"],1)
        self.assertEqual(summary(eps)["gross_positive_but_net_nonpositive"],1)

    def test_short_profitable(self):
        x=[fill("sell","SELL","10","2","0.2"),
           fill("buy","BUY","10","1","0.2")]
        eps,_,book,_=attribution(x,{"sell":1000,"buy":3000})
        self.assertEqual(eps[0]["net_usd"],"9.6")
        self.assertEqual(book.quantity,0)
        self.assertEqual(summary(eps)["net_positive_roundtrips"],1)

    def test_partial_entry_and_exit(self):
        x=[fill("e1","BUY","5","1","0.5"),fill("e2","BUY","5","1","0.5"),
           fill("x1","SELL","3","1.1","1"),fill("x2","SELL","7","1.1","1")]
        eps,opened,book,_=attribution(x,{n:i*1000 for i,n in enumerate(["e1","e2","x1","x2"],1)})
        self.assertEqual(len(eps),1)
        self.assertEqual(eps[0]["entry_quantity"],"10")
        self.assertEqual(eps[0]["execution_count"],4)
        self.assertEqual(book.realized,1)
        self.assertEqual(book.paid_fees,3)
        self.assertFalse(opened)

    def test_open_episode_not_marked_realized(self):
        eps,opened,book,_=attribution([fill("entry","BUY","5","1","2")],{"entry":1})
        self.assertEqual(eps,[])
        self.assertTrue(opened)
        self.assertEqual(book.quantity,5)
        self.assertEqual(book.realized,0)

    def test_cross_zero_flip_refused(self):
        x=[fill("open","BUY","10","1","1"),fill("bad","SELL","11","1.1","1")]
        with self.assertRaisesRegex(ValueError,"CROSS_ZERO"):
            attribution(x,{"open":1,"bad":2})

    def test_incomplete_fee_refused(self):
        with self.assertRaisesRegex(ValueError,"INCOMPLETE_EXECUTION"):
            attribution([fill("x","BUY","10","1",None)],{})

    def test_nonflat_period_refused(self):
        with self.assertRaisesRegex(ValueError,"NON_FLAT"):
            attribution([] ,{},opening_quantity="1")

    def test_missing_original_send_is_not_invented(self):
        eps,opened,book,missing=attribution([
           fill("entry","SELL","10","2","0.1"),fill("exit","BUY","10","1","0.1")],{})
        self.assertEqual(missing,2)
        self.assertIsNone(eps[0]["send_to_send_seconds"])


if __name__=="__main__":
    unittest.main()
