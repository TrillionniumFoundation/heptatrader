"""Offline evidence reuse must preserve identity and original model predictions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from laya_cell.integrity import ContractError, canonical, digest
from laya_cell.resident_model import ResidentDecisionCell
from laya_cell.resident_price_input import RAW
from laya_cell import resident_price_feature_snapshot as snapshot
from laya_cell.resident_train import _frozen_price_logits
from laya_cell.tests.test_resident_price_cache import RawDecisionModel, encode, rows


class PriceFeatureSnapshotTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(20261128)
        self.cell = ResidentDecisionCell(RawDecisionModel(), 'a' * 64)
        self.cell.eval()
        self.rows = rows()[:4]
        for row in self.rows:
            row['state'] = {'instrument': 'SYNTHETIC.TEST', 'anchor_ms': row['index'],
                            'reference_mid': 1.25, 'horizon_sec': 300,
                            'columns': ['mid'], 'quotes': [[1.25]],
                            'quantity': 'synthetic fixture'}
        self.parent = {'candidate_id': 'b' * 64, 'base_id': 'a' * 64,
                       'base_revision': 'c' * 40, 'adapter_sha256': 'd' * 64}
        self.config = {snapshot.FIELD: snapshot.PROFILE,
            'objective': {'schema': 'heptatrader.laya-resident-objective.v8'},
            'runtime_sha256': {name: digest(name) for name in snapshot.PINS},
            'parent_id': self.parent['candidate_id'], 'parent_manifest_sha256': 'e' * 64,
            'dataset_sha256': 'f' * 64}
        self.forward = _frozen_price_logits(self.cell, encode, torch.device('cpu'), RAW)

    def prime(self):
        with torch.no_grad():
            return [self.forward([row]).clone() for row in self.rows]

    def prepare(self):
        return snapshot.prepare(self.config, self.parent, 'a' * 64, self.rows, self.forward)

    def context(self):
        return snapshot.binding(self.config, self.parent, 'a' * 64, self.rows)

    def test_export_load_and_edited_scorer_replay_equal_full_singleton_forwards(self):
        original = self.prime()
        calls = self.cell.model.encoder.calls
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self.prepare()
            receipt = snapshot.publish(temporary, prepared)
            tensors, manifest = snapshot.load_snapshot(temporary, receipt, self.context())
            self.assertEqual(receipt['state'], 'STORED')
            self.assertEqual(self.cell.model.encoder.calls, calls)
            self.assertEqual(manifest['extra_encoder_forwards'], 0)
            self.assertFalse(manifest['held_out'])
            self.assertEqual(Path(temporary, snapshot.FILE).stat().st_mode & 0o777, 0o400)
            with torch.no_grad():
                for step in range(2):
                    for i, row in enumerate(self.rows):
                        features = tensors['features'][i:i+1]
                        mask = tensors['marker_mask'][i:i+1]
                        replay = self.cell.model.scorer(features).squeeze(-1).float().masked_fill(~mask, -1e4)
                        full = self.cell(**encode([row]))[0]
                        self.assertTrue(torch.equal(replay, full))
                        if step == 0:
                            self.assertTrue(torch.equal(replay, original[i]))
                    for name, value in self.cell.model.scorer.named_parameters():
                        if name.endswith('.B'):
                            value.add_(.01)

    def test_copy_owns_storage_and_never_changes_live_cache(self):
        original = self.prime()
        exported = self.forward.snapshot(self.rows)
        exported['features'].zero_()
        exported['marker_mask'].zero_()
        with torch.no_grad():
            actual = [self.forward([row]) for row in self.rows]
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(original, actual)))
        self.assertEqual(self.cell.model.encoder.calls, len(self.rows))

    def test_missing_eval_singleton_is_rejected_without_extra_forward(self):
        self.forward(self.rows)
        calls = self.cell.model.encoder.calls
        with self.assertRaisesRegex(ContractError, 'SINGLETON_MISSING'):
            self.prepare()
        self.assertEqual(self.cell.model.encoder.calls, calls)
        for row in self.rows:
            self.forward([row])  # grad-enabled singleton is a different contract.
        with self.assertRaisesRegex(ContractError, 'SINGLETON_MISSING'):
            self.prepare()

    def test_changed_row_duplicate_row_or_upstream_cannot_be_exported(self):
        self.prime()
        changed = copy.deepcopy(self.rows)
        changed[0]['question'] = 'different question'
        with self.assertRaisesRegex(ContractError, 'INPUT_CHANGED'):
            self.forward.snapshot(changed)
        with self.assertRaisesRegex(ContractError, 'ROW_SET'):
            self.forward.snapshot([self.rows[0], self.rows[0]])
        with torch.no_grad():
            self.cell.model.encoder.weight.add_(.1)
        with self.assertRaisesRegex(ContractError, 'UPSTREAM_CHANGED'):
            self.prepare()

    def test_identity_changes_reject_before_replay(self):
        self.prime()
        with tempfile.TemporaryDirectory() as temporary:
            receipt = snapshot.publish(temporary, self.prepare())
            original = self.context()
            for field in ('dataset_sha256', 'parent_id', 'base_id', 'runtime_inventory_digest'):
                changed = copy.deepcopy(original)
                changed[field] = '0' * 64
                with self.subTest(field=field), self.assertRaisesRegex(ContractError, 'RECEIPT'):
                    snapshot.load_snapshot(temporary, receipt, changed)
            changed = copy.deepcopy(original)
            changed['rows'].reverse()
            with self.assertRaisesRegex(ContractError, 'RECEIPT'):
                snapshot.load_snapshot(temporary, receipt, changed)

    def test_corrupted_manifest_or_tensor_file_rejects(self):
        self.prime()
        for name in (snapshot.FILE, snapshot.MANIFEST):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                receipt = snapshot.publish(temporary, self.prepare())
                path = Path(temporary, name)
                original = path.read_bytes()
                path.chmod(0o600)
                path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                path.chmod(0o400)
                with self.assertRaisesRegex(ContractError, 'HASH_CHANGED'):
                    snapshot.load_snapshot(temporary, receipt, self.context())

    def test_no_overwrite_or_symlink_follow_and_partial_archive_unusable(self):
        self.prime()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            target = folder / 'unrelated'
            target.write_bytes(b'preserve existing bytes')
            (folder / snapshot.FILE).symlink_to(target)
            receipt = snapshot.publish(folder, self.prepare())
            self.assertEqual(receipt['state'], 'NOT_STORED')
            self.assertEqual(target.read_bytes(), b'preserve existing bytes')
            self.assertFalse((folder / snapshot.MANIFEST).exists())
            with self.assertRaisesRegex(ContractError, 'RECEIPT'):
                snapshot.load_snapshot(folder, receipt, self.context())
        with tempfile.TemporaryDirectory() as temporary:
            receipt = snapshot.publish(temporary, self.prepare())
            first = Path(temporary, snapshot.FILE).read_bytes()
            failed = snapshot.publish(temporary, self.prepare())
            self.assertEqual(failed['state'], 'NOT_STORED')
            self.assertEqual(Path(temporary, snapshot.FILE).read_bytes(), first)
            snapshot.load_snapshot(temporary, receipt, self.context())

    def test_mid_publish_failure_does_not_authorize_partial_replay(self):
        self.prime()
        original = snapshot._write_new
        calls = []
        def fail_second(path, raw):
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, 'fixture storage full')
            original(path, raw)
        with tempfile.TemporaryDirectory() as temporary, patch.object(snapshot, '_write_new', fail_second):
            receipt = snapshot.publish(temporary, self.prepare())
            self.assertEqual(receipt['state'], 'NOT_STORED')
            self.assertEqual(receipt['errno'], 28)
            self.assertTrue(Path(temporary, snapshot.FILE).is_file())
            with self.assertRaisesRegex(ContractError, 'RECEIPT'):
                snapshot.load_snapshot(temporary, receipt, self.context())

    def test_strict_manifest_decode_and_nonfinite_payload_checks(self):
        self.prime()
        with tempfile.TemporaryDirectory() as temporary:
            raw, manifest = self.prepare()
            value = json.loads(manifest)
            malicious = manifest[:-1] + b',"schema":"heptatrader.price-feature-snapshot.v1"}'
            with self.assertRaisesRegex(ContractError, 'DUPLICATE_JSON_KEY'):
                snapshot.publish(temporary, (raw, malicious))
        from safetensors.torch import load, save
        tensors = load(raw)
        tensors['features'][0, 0, 0] = float('nan')
        raw = save(tensors)
        value.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        value.pop('record_digest')
        value['record_digest'] = digest(value)
        with tempfile.TemporaryDirectory() as temporary:
            receipt = snapshot.publish(temporary, (raw, canonical(value)))
            with self.assertRaisesRegex(ContractError, 'NONFINITE_OR_EMPTY'):
                snapshot.load_snapshot(temporary, receipt, self.context())

    def test_unknown_profile_unpinned_runtime_and_wrong_parent_fail_closed(self):
        self.assertFalse(snapshot.check_config({}))
        for update in ({snapshot.FIELD: 'UNKNOWN'}, {'objective': {'schema': 'v7'}},
                       {'runtime_sha256': {}}, {'runtime_sha256': {n: 'z' * 64 for n in snapshot.PINS}}):
            with self.subTest(update=update), self.assertRaises(ContractError):
                snapshot.check_config({**self.config, **update})
        with self.assertRaisesRegex(ContractError, 'PARENT_OR_BASE_CHANGED'):
            snapshot.binding(self.config, self.parent, '9' * 64, self.rows)

    def test_snapshot_contains_input_hashes_and_no_quote_or_target_copy(self):
        self.prime()
        _, raw = self.prepare()
        decoded = raw.decode()
        for forbidden in ('SYNTHETIC.TEST', 'synthetic fixture', 'target_class', 'teacher_probs'):
            self.assertNotIn(forbidden, decoded)

    def test_enabled_full_v8_job_preserves_gradients_adapter_and_encoder_count(self):
        from laya_cell import resident_train as worker
        from laya_cell.integrity import read_json
        from laya_cell.resident_price_calibration import training_rows_for_job
        from laya_cell.tests import test_resident_training_v8 as integration
        run_job = worker.run_job
        cache_factory = worker._frozen_price_logits
        observations = []
        def run_counted(job, **kwargs):
            hooks, calls = [], []
            def make_cache(cell, encoded, device, profile):
                hooks.append(cell.model.encoder.register_forward_hook(lambda *_: calls.append(1)))
                return cache_factory(cell, encoded, device, profile)
            try:
                with patch.object(worker, '_frozen_price_logits', side_effect=make_cache):
                    result = run_job(job, **kwargs)
            finally:
                for hook in hooks:
                    hook.remove()
            return result, len(calls)
        def compare(job, **kwargs):
            baseline_job = {**job, 'output_directory':
                str(Path(job['output_directory']).with_name('without-snapshot'))}
            expected, baseline_count = run_counted(baseline_job, **kwargs)
            job[snapshot.FIELD] = snapshot.PROFILE
            job['runtime_sha256'] = {**job.get('runtime_sha256', {}),
                                     **{name: digest(name) for name in snapshot.PINS}}
            actual, enabled_count = run_counted(job, **kwargs)
            self.assertEqual(baseline_count, enabled_count)
            for key in ('steps', 'action_phase', 'adapter_sha256',
                        'parent_price_forward_receipts', 'initial_child_price_forward_receipts',
                        'final_price_raw_forward_receipts', 'initial_child_price_measurement',
                        'measurement', 'price_calibration', 'price_calibration_training_measurement'):
                self.assertEqual(expected[key], actual[key], key)
            parent = read_json(Path(job['parent_directory']) / 'candidate.json')
            selected = training_rows_for_job(job)
            context = snapshot.binding(job, parent, actual['base_id'], selected)
            tensors, _ = snapshot.load_snapshot(job['output_directory'], actual[snapshot.RECEIPT], context)
            self.assertEqual(tensors['features'].shape[0], 32)
            self.assertEqual(actual['candidate_id'], digest({k: v for k, v in actual.items() if k != 'candidate_id'}))
            observations.append(enabled_count)
            return actual
        with patch.object(worker, 'run_job', side_effect=compare):
            integration.RawCenteredIntegrationTests.fixture(self, True, parent_comparator=True)
        self.assertEqual(len(observations), 1)


if __name__ == '__main__':
    unittest.main()
