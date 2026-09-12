"""JSON numeric overflow/underflow must not become valid evidence or flatness."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from hepta_evidence_io import EvidenceError, loads


class EvidenceNumericIngestionTests(unittest.TestCase):
    def test_exponent_overflow_is_rejected_at_ingestion(self):
        for token in ('1e400', '-1e400', 'NaN', 'Infinity', '-Infinity'):
            with self.subTest(token=token), self.assertRaises(EvidenceError):
                loads('{"nested":{"quantity":'+token+'}}')

    def test_nonzero_underflow_cannot_turn_a_position_into_flat(self):
        for token in ('1e-4000', '-1e-4000', '1E-9999999'):
            with self.subTest(token=token), self.assertRaises(EvidenceError):
                loads('{"position_quantity":'+token+'}')

    def test_real_zero_and_finite_numbers_remain_accepted(self):
        self.assertEqual(loads('[0,-0.0,0e-4000,-0e9999,1.25,5e-324]'),
                         [0, -0.0, 0.0, -0.0, 1.25, 5e-324])


if __name__ == '__main__':
    unittest.main()
