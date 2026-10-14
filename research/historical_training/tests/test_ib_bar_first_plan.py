import unittest
from datetime import datetime,timezone
from historical_training.ib_history import plans

class PlanTests(unittest.TestCase):
 def test_default_still_uses_three_heads(self):
  ps=plans({'chunks':1,'window_days':7},datetime(2026,10,13,tzinfo=timezone.utc))
  self.assertEqual([p['kind'] for p in ps[:3]],['head']*3)
  self.assertEqual(len(ps),7)
 def test_explicit_bar_first_no_head_queries(self):
  ps=plans({'chunks':1,'window_days':7,'skip_head_timestamp_requests':True},
           datetime(2026,10,13,tzinfo=timezone.utc))
  self.assertEqual([p['what'] for p in ps],['MIDPOINT','BID','ASK','SCHEDULE'])
  self.assertEqual([p['kind'] for p in ps],['bars','bars','bars','schedule'])
  self.assertEqual(ps[0]['duration'],'7 D')
if __name__=='__main__':unittest.main()
