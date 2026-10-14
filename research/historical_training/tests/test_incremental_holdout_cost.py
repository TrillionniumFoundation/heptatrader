import json,tempfile,unittest
from pathlib import Path
from historical_training.incremental_holdout_cost import evaluate

class ProofTests(unittest.TestCase):
 def test_refuses_false_increment_identity_without_fitting(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   old=root/'old.json';old.write_text(json.dumps({'bars':[{'time':1000}]}))
   new=root/'new.json';new.write_text(json.dumps({'bars':[{'time':1000},{'time':1300}]}))
   proof=root/'proof.json';proof.write_text(json.dumps({
      'overlap_bars_identical':1,'revision_conflicts':0,
      'new_bars_from_increment':20,'new_total_bars':2}))
   with self.assertRaisesRegex(ValueError,'NEW_HISTORY_PROOF_OR_TIME_CONFLICT'):
    evaluate(old,new,proof)
if __name__=='__main__':unittest.main()
