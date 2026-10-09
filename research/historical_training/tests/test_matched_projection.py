import unittest
from historical_training.common import EvidenceError
from historical_training.matched_projection import validate_projection
class MatchedProjectionContractTests(unittest.TestCase):
    def test_multilingual_fixed64(self):validate_projection(768,64)
    def test_english_fixed64(self):validate_projection(1024,64)
    def test_old256_contract_cannot_silently_replace_plan(self):
        with self.assertRaises(EvidenceError):validate_projection(768,256)
    def test_unknown_encoder_width_denied(self):
        with self.assertRaises(EvidenceError):validate_projection(384,64)
    def test_fractional_width_not_coerced(self):
        with self.assertRaises(EvidenceError):validate_projection(768.5,64)
    def test_boolean_destination_not_valid(self):
        with self.assertRaises(EvidenceError):validate_projection(768,True)
if __name__=='__main__':unittest.main()
