"""Frozen inference identity and numeric shape, without importing Torch."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'research/neuron_learning'))
from laya_cell.integrity import ContractError
import numpy as np
from safetensors.numpy import save
from laya_cell.frozen_price_numpy import FrozenPriceModel

class FrozenPriceTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'model.safetensors'
        tensors = {'0.weight': np.zeros((32, 24), np.float32), '0.bias': np.zeros(32, np.float32), '2.weight': np.zeros((7, 32), np.float32), '2.bias': np.arange(7, dtype=np.float32)}
        self.raw = save(tensors)
        self.path.write_bytes(self.raw)
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.norm = {'mean': [0.0] * 24, 'std': [1.0] * 24, 'clip': 6, 'train_only': True}

    def test_exact_frozen_bytes_produce_expected_probabilities(self):
        model = FrozenPriceModel(self.path, self.sha, self.norm)
        self.path.write_bytes(b'subsequent path replacement')
        probs, clipped = model.predict(np.zeros((2, 24), np.float32))
        expected = np.exp(np.arange(7, dtype=np.float32) - 6)
        expected /= expected.sum()
        np.testing.assert_allclose(probs, np.tile(expected, (2, 1)), rtol=1e-06)
        self.assertEqual(clipped.tolist(), [0, 0])
        self.assertFalse(any((a.flags.writeable for a in model.tensors.values())))

    def test_changed_weight_bytes_are_rejected(self):
        self.path.write_bytes(self.raw + b'changed')
        with self.assertRaisesRegex(ContractError, 'FROZEN_PRICE_WEIGHTS_CHANGED'):
            FrozenPriceModel(self.path, self.sha, self.norm)
if __name__ == '__main__':
    unittest.main()
