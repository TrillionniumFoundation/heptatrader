import unittest
import sys
from unittest.mock import patch
from laya_cell import source_readiness
from laya_cell.source_readiness import next_monitor_deadline, RULE
from laya_cell.integrity import ContractError

class CadenceTests(unittest.TestCase):
    def test_default_preserved(self):
        self.assertEqual(next_monitor_deadline(60,100.,117.),177.)
    def test_fast_start_to_start(self):
        self.assertEqual(next_monitor_deadline(15,100.,108.),115.)
    def test_fast_overrun_recovery(self):
        self.assertEqual(next_monitor_deadline(15,100.,118.),119.)
    def test_minimum_and_maximum(self):
        self.assertEqual(next_monitor_deadline(10,100.,105.),110.)
        self.assertEqual(next_monitor_deadline(60,100.,105.),165.)
    def test_bad_cadence_rejected(self):
        for x in (-1,0,1,9,61,True,15.5):
            with self.subTest(value=x),self.assertRaises(ContractError):
                next_monitor_deadline(x,100.,105.)
    def test_clock_reverse_refused(self):
        with self.assertRaises(ContractError):
            next_monitor_deadline(15,110.,100.)
    def test_original_health_rules_unchanged(self):
        self.assertEqual(RULE['input_probes'],24)
        self.assertEqual(RULE['minimum_usable'],20)
        self.assertEqual(RULE['maximum_missing'],4)
        self.assertEqual(RULE['source_ttl_ms'],5000)
        self.assertEqual(RULE['path_gap_max_ms'],30000)
        self.assertEqual(RULE['endpoint_tolerance_ms'],5000)

    def test_fast_refresh_without_sandbox_refused(self):
        args=['test','--config','/unavailable','--output','/unavailable',
              '--refresh-seconds','15']
        with patch.object(sys,'argv',args),self.assertRaises(SystemExit) as ex:
            source_readiness.main()
        self.assertEqual(ex.exception.code,2)
    def test_finite_monitor_without_sandbox_refused(self):
        args=['test','--config','/unavailable','--output','/unavailable',
              '--max-cycles','4']
        with patch.object(sys,'argv',args),self.assertRaises(SystemExit) as ex:
            source_readiness.main()
        self.assertEqual(ex.exception.code,2)

if __name__=='__main__':unittest.main()
