"""Immutable incremental current100k reward export without legacy snapshot pruning."""
import json
import tempfile
import unittest
from pathlib import Path
from historical_training.common import EvidenceError,read,sha
from historical_training.export_current100k import publish_incremental


class IncrementalConsumerTests(unittest.TestCase):
    def payload(self, *ids):
        return {'schema':'heptatrader.current100k-value-dataset.v1',
                'period_sha256':'p','producer_manifest_sha256':'m',
                'rows':[{'episode_id':i,'row_id':'row-'+i,'net_usd':'-3.25'}
                        for i in ids], 'trade_authority':False}

    def test_append_only_idempotent_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            legacy=root/'datasets'/'old.json'
            legacy.parent.mkdir()
            legacy.write_bytes(b'immutable old snapshot\n')
            p,rec=publish_incremental(root,self.payload('e1'),16*1024**2)
            original=sha(p)
            self.assertEqual(rec['new_episodes'],1)
            self.assertEqual(rec['durable_episodes'],1)
            _,again=publish_incremental(root,self.payload('e1'),16*1024**2)
            self.assertEqual(again['new_episodes'],0)
            self.assertEqual(sha(p),original)
            p,last=publish_incremental(root,self.payload('e1','e2'),16*1024**2)
            self.assertEqual(last['new_episodes'],1)
            self.assertEqual(last['durable_episodes'],2)
            self.assertEqual(len(list((root/'incremental-v1'/'episodes').glob('*.json'))),2)
            self.assertEqual(len(read(p)['rows']),2)
            self.assertEqual(legacy.read_bytes(),b'immutable old snapshot\n')

    def test_rewrite_and_withdrawal_fail_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            p,_=publish_incremental(root,self.payload('e1','e2'),16*1024**2)
            before=sha(p)
            revised=self.payload('e1','e2')
            revised['rows'][0]['net_usd']='+100000'
            for corrupt in (revised,self.payload('e2')):
                with self.assertRaisesRegex(EvidenceError,'INCREMENTAL_REWARD_REWRITTEN_OR_WITHDRAWN'):
                    publish_incremental(root,corrupt,16*1024**2)
                self.assertEqual(sha(p),before)

    def test_budget_rejected_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with self.assertRaisesRegex(EvidenceError,'INCREMENTAL_STORAGE_BUDGET_INVALID'):
                publish_incremental(root,self.payload('e1'),1)
            store=root/'incremental-v1'
            store.mkdir(parents=True)
            (store/'dataset-current.json').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(EvidenceError,'INCREMENTAL_VIEW_INVALID'):
                publish_incremental(root,self.payload('e1'),16*1024**2)


if __name__=='__main__':
    unittest.main()
