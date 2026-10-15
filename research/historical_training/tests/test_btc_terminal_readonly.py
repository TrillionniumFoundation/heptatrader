"""Full order-identity matching for read-only BTC terminal observation."""
import unittest
from historical_training.btc_terminal_probe import TARGETS,match_btc_orders

class BrokerTerminalMatchTests(unittest.TestCase):
    def test_all_four_original_commands_are_accounted_for(self):
        self.assertEqual(len(TARGETS),4)
        self.assertEqual(len(set(TARGETS)),4)

    def test_matching_needs_permanent_id_conid_reference_and_account(self):
        good={'conid':479624278,'currency':'USD','account_matches':True,
              'order_id':999,'permanent_id':1234,'order_ref':'hepta:original'}
        rows=[good,{**good,'order_ref':'forged'},
              {**good,'permanent_id':1235},
              {**good,'conid':111},{**good,'currency':'EUR'},
              {**good,'account_matches':False}]
        self.assertEqual(match_btc_orders(rows,'DU-TEST','hepta:original',{1234}),[good])
        self.assertEqual(match_btc_orders(rows,'DU-TEST','hepta:original',{9999}),[])
        self.assertEqual(match_btc_orders(rows,'DU-TEST','forged',{1234}),[rows[1]])

if __name__=='__main__':
    unittest.main()
