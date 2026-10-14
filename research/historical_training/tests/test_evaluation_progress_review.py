import unittest
from historical_training.evaluation_progress_review import classify
class ProgressTest(unittest.TestCase):
 def test_categories_do_not_mix(self):
  r=classify([{'command_id':'a','status':'OBSERVED'},{'command_id':'b','status':'PENDING','reason':'AWAIT_EXACT_CLOSE'},{'command_id':'c','status':'CENSORED','reason':'LATE_PREDICTION'}])
  self.assertEqual((r['observed'],r['pending'],r['censored']),(1,1,1));self.assertEqual(r['pending_by_reason'],{'AWAIT_EXACT_CLOSE':1})
 def test_duplicate_commands_refused(self):
  with self.assertRaises(ValueError):classify([{'command_id':'a','status':'OBSERVED'}]*2)
 def test_unknown_not_success(self):
  with self.assertRaises(ValueError):classify([{'command_id':'a','status':'READY'}])
 def test_no_records_is_not_success(self):
  r=classify([]);self.assertEqual(r['observed'],0)
if __name__=='__main__':unittest.main()
