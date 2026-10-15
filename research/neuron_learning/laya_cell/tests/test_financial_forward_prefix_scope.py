"""Synthetic OMS size regression; no broker connection or orders."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from laya_cell.financial_forward_evaluate import execution_index
from laya_cell.integrity import ContractError

class FinancialForwardPrefixScopeTests(unittest.TestCase):
    def source(self, path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)
        return {'path':str(path), 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}

    def test_valid_private_oms_larger_than_32_mib(self):
        event = {'event':'broker_execution', 'broker_execution_id':'eid',
                 'ts_ms':1000, 'execution_domain':'PAPER',
                 'instrument':'EUR.USD', 'req_id':'cmd'}
        # Legal JSONL record; whitespace crosses the old 32 MiB file cap.
        raw = json.dumps(event).encode() + b' ' * (32 * 1024**2) + b'\n'
        with tempfile.TemporaryDirectory() as tmp:
            by_id,by_cmd,linked=execution_index(self.source(Path(tmp)/'oms.jsonl',raw))
            self.assertEqual(by_id, {'eid':1000})
            self.assertEqual(by_cmd, {'cmd':1000})
            self.assertEqual(linked, {'eid':'cmd'})

    def test_pinned_prefix_mutation_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'oms.jsonl'
            source=self.source(path,b'{}\n')
            path.write_bytes(b'{ }\n')
            with self.assertRaisesRegex(ContractError, 'FINANCIAL_LEDGER_PREFIX_CHANGED'):
                execution_index(source)

    def test_private_permissions_and_symlink_remain_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'oms.jsonl'
            source=self.source(path,b'{}\n')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'ATTRIBUTION_FILE_SCOPE'):
                execution_index(source)
            path.chmod(0o600)
            link=Path(tmp)/'alias.jsonl'
            link.symlink_to(path)
            source['path']=str(link)
            with self.assertRaises(OSError):
                execution_index(source)

    def test_more_than_canonical_64_mib_remains_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'oms.jsonl'
            source=self.source(path,b'{}\n')
            with path.open('r+b') as f:
                f.truncate(64*1024**2+1)
            with self.assertRaisesRegex(ValueError, 'ATTRIBUTION_FILE_SCOPE'):
                execution_index(source)

if __name__=='__main__':
    unittest.main()
