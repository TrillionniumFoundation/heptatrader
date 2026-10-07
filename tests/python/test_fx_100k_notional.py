import importlib.util,math,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[2]/"scripts/heptatrader_paper_driver.py"
spec=importlib.util.spec_from_file_location("fx100k_driver",P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Fx100kTests(unittest.TestCase):
 def test_usd_notional_never_exceeds_100k(self):
  for price in (.85,1.0,1.11861,1.5,2.0):
   q=m.fx_order_quantity(price)
   self.assertGreater(q,0);self.assertLessEqual(q,m.FX_MAX_QUANTITY)
   self.assertLessEqual(q*price,m.FX_TARGET_NOTIONAL_USD+1e-9)
 def test_current_eurusd_size_is_about_89k_not_100k_eur(self):
  q=m.fx_order_quantity(1.11861)
  self.assertEqual(q,math.floor(100000/1.11861))
  self.assertGreater(q,89000);self.assertLess(q,90000)
 def test_invalid_quote_fails_closed(self):
  for x in (0,-1,float("inf"),float("nan")):
   with self.assertRaises(m.DriverError):m.fx_order_quantity(x)
 def test_original_entry_predicate_unchanged(self):
  self.assertTrue(m.entry_candidate_allowed({"action":"BUY","confidence":.62}))
  self.assertTrue(m.entry_candidate_allowed({"action":"SELL","confidence":.62}))
  self.assertFalse(m.entry_candidate_allowed({"action":"BUY","confidence":.6199}))
  self.assertFalse(m.entry_candidate_allowed({"action":"HOLD","confidence":1.0}))
if __name__=="__main__":unittest.main(verbosity=2)
