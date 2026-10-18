import math
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research/neuron_learning'))
from laya_cell import original_price_source as s
from laya_cell.integrity import ContractError

def fixture(tmp_path):
    feed = tmp_path / 'feed'
    feed.mkdir()
    paths = []
    for name in ('segment-a', 'segment-b'):
        folder = feed / name
        folder.mkdir()
        p = folder / 'quotes.jsonl'
        p.write_text('')
        paths.append(p)
    cfg = {'period_path': str(tmp_path / 'period'), 'period_sha256': 'a' * 64, 'feed_directory': str(feed), 'completed_history_sources': []}
    now = 10000000
    return (cfg, paths, now)

class OriginalPriceSourceWindowTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp_path = Path(directory.name)

    def test_old_sealed_only_skips_decoding_after_archive_verification(self):
        tmp_path = self.tmp_path
        cfg, paths, now = fixture(tmp_path)
        pin = {'path': str(paths[0]), 'ended': True}
        quote = {'t': now - 1000, 'known_at_ms': now - 500, 'bid': 1.0, 'ask': 1.1, 'mid': 1.05}
        with patch.object(s, 'sha_file', return_value='a' * 64), patch.object(s, 'inventory', return_value=([pin], [])) as inventory, patch.object(s, '_sealed_end_timestamp', return_value=1), patch.object(s, 'verify_prefix') as verify, patch.object(s, 'read_source', return_value=({'period_sha256': 'a' * 64}, [quote], {'path': str(paths[1])})) as read:
            quotes, proof = s.read_original_price_window(cfg, now)
            assert len(quotes) == 1 and proof['old_sealed_segments_fully_rehashed_not_decoded'] == 1
            assert read.call_count == 1 and inventory.call_count == 2 and (verify.call_count == 1)

    def test_late_receipt_never_enters_cutoff(self):
        tmp_path = self.tmp_path
        cfg, paths, now = fixture(tmp_path)
        records = [{'t': now - 2000, 'known_at_ms': now - 1000, 'bid': 1, 'ask': 1.1, 'mid': 1.05}, {'t': now - 1000, 'known_at_ms': now + 1, 'bid': 1, 'ask': 1.1, 'mid': 1.05}]
        with patch.object(s, 'sha_file', return_value='a' * 64), patch.object(s, 'inventory', return_value=([], [])), patch.object(s, 'read_source', return_value=({'period_sha256': 'a' * 64}, records, {})):
            quotes, _ = s.read_original_price_window(cfg, now)
            assert [q['t'] for q in quotes] == [now - 2000]

    def test_archive_rotation_is_hard_error(self):
        tmp_path = self.tmp_path
        cfg, paths, now = fixture(tmp_path)
        with patch.object(s, 'sha_file', return_value='a' * 64), patch.object(s, 'inventory', side_effect=[([], []), ([], [{'changed': True}])]), patch.object(s, 'read_source', return_value=({'period_sha256': 'a' * 64}, [], {})):
            with self.assertRaisesRegex(ContractError, 'ARCHIVE_CHANGED'):
                s.read_original_price_window(cfg, now)

    def test_integrity_error_never_retried_with_weaker_reader(self):
        tmp_path = self.tmp_path
        cfg, paths, now = fixture(tmp_path)
        with patch.object(s, 'sha_file', return_value='a' * 64), patch.object(s, 'inventory', side_effect=ContractError('CORRUPT_ARCHIVE')), patch.object(s, 'read_source') as read:
            with self.assertRaisesRegex(ContractError, 'CORRUPT'):
                s.read_original_price_window(cfg, now)
            assert not read.called
if __name__ == '__main__':
    unittest.main()
