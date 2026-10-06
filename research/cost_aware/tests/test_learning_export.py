import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hepta_learning_export import LearningExport, projected_state, digest


class LearningExportTests(unittest.TestCase):
    def test_projection_excludes_credentials_and_preserves_original_command(self):
        state={'instrument':'EUR.USD','position':0,'account':'SECRET','token':'SECRET',
          'pending':{'command_id':'original','side':'BUY','preview_permit':'SECRET','request_sha256':'a'*64},
          'last_reconciliation':{'command_id':'original','execution_ids':['exec1'],'token':'SECRET'},
          'gross_pnl':4,'net_pnl':4}
        out=projected_state(state)
        self.assertNotIn('SECRET',json.dumps(out))
        self.assertEqual(out['pending']['command_id'],'original')
        self.assertEqual(out['reconciliation']['execution_ids'],['exec1'])
        self.assertFalse(out['fees_complete']);self.assertIsNone(out['net_pnl'])

    def test_actual_net_requires_explicit_fee_completeness(self):
        out=projected_state({'fees_complete':True,'net_pnl':2})
        self.assertEqual(out['net_pnl'],2)

    def test_append_chain_and_exact_input_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            w=LearningExport(root,'EUR.USD')
            data={'quotes':[{'t':10,'bid':1.0,'ask':1.1}]}
            self.assertTrue(w.emit('MODEL_INPUT',data));data['quotes'][0]['bid']=999
            self.assertTrue(w.emit('DECISION',{'pending':None}))
            self.assertTrue(w.emit('DECISION',{'pending':None}))
            w.close()
            rows=[json.loads(x) for p in Path(root).glob('*.jsonl') for x in p.read_text().splitlines()]
            self.assertEqual(len(rows),3)
            previous='0'*64
            for seq,row in enumerate(rows):
                self.assertEqual(row['sequence'],seq);self.assertEqual(row['previous'],previous)
                self.assertEqual(row['sha256'],digest({k:v for k,v in row.items() if k!='sha256'}))
                previous=row['sha256']
            self.assertEqual(rows[1]['data']['quotes'][0]['bid'],1.0)
            self.assertFalse(rows[0]['data']['broker_mutation_authorized'])
            self.assertEqual(rows[0]['data']['execution_domain'],'PAPER')
            self.assertEqual(next(Path(root).glob('*.jsonl')).stat().st_mode&0o777,0o600)

    def test_full_queue_is_observable_not_blocking(self):
        with tempfile.TemporaryDirectory() as root, patch.object(LearningExport,'_worker',lambda s:s.stop.wait(2)):
            w=LearningExport(root,'EUR.USD',capacity=1)
            self.assertTrue(w.emit('QUOTE',{'bid':1}))
            self.assertFalse(w.emit('QUOTE',{'bid':2}));self.assertEqual(w.dropped,1)
            w.close()

    def test_oversize_and_nonfinite_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            w=LearningExport(root,'EUR.USD')
            self.assertFalse(w.emit('QUOTE',{'x':'x'*70000}))
            self.assertFalse(w.emit('QUOTE',{'x':float('nan')}))
            self.assertFalse(w.emit('ORDER_SEND',{}))
            w.close();self.assertEqual(w.dropped,3)

    def test_public_directory_rejected_independent_of_umask(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'public';p.mkdir();p.chmod(0o755)
            with self.assertRaisesRegex(ValueError,'DIRECTORY'):LearningExport(p,'EUR.USD')

    def test_invalid_instrument_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):LearningExport(root,'LIVE')

    def test_closed_export_does_not_accept_more_data(self):
        with tempfile.TemporaryDirectory() as root:
            w=LearningExport(root,'BTC.USD');w.close()
            self.assertFalse(w.emit('QUOTE',{}))

    def test_no_command_transport_or_gpu_import(self):
        import hepta_learning_export
        source=Path(hepta_learning_export.__file__).read_text()
        for needle in ('subprocess.','torch','hipLaunchKernel','paper-on','systemctl'):
            self.assertNotIn(needle,source)
