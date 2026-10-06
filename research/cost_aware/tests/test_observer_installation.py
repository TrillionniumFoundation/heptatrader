"""Inert research packaging boundaries, no installation or service mutation."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]

class ObserverInstallationTests(unittest.TestCase):
    def test_template_starts_no_trading_dependency(self):
        text = (ROOT/'systemd/user/heptatrader-cost-observer@.service').read_text()
        self.assertNotIn('Requires=', text)
        self.assertNotIn('Wants=', text)
        self.assertNotIn('PartOf=', text)
        self.assertIn('--sample-sec 2', text)
        self.assertEqual(text.count('--sample-sec'), 1)
        self.assertIn('/cost-observer/%i', text)
        self.assertIn('RestartPreventExitStatus=2', text)
    def test_optional_unit_is_excluded_from_incomplete_runtime_package(self):
        text = (ROOT/'cmake/HeptaInstall.cmake').read_text()
        self.assertIn('PATTERN "heptatrader-cost-observer@.service" EXCLUDE', text)
        self.assertIn('COMPONENT CostResearch EXCLUDE_FROM_ALL', text)
    def test_invalid_poll_interval_rejected_before_any_io(self):
        cmd = [sys.executable, '-B', str(ROOT/'research/cost_aware/scripts/hepta_cost_observer.py'),
               '--base-driver', '/not-a-driver', '--instrument', 'BTC.USD',
               '--state-dir', '/not-a-state', '--sample-sec', '3']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)
    def test_invalid_instance_rejected_before_any_io(self):
        cmd = [sys.executable, '-B', str(ROOT/'research/cost_aware/scripts/hepta_cost_observer.py'),
               '--base-driver', '/not-a-driver', '--instrument', 'UNSUPPORTED',
               '--state-dir', '/not-a-state']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)
