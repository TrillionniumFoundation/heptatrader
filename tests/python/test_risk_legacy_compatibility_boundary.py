"""Compiler-backed regression for the retired unbound risk API."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
RETIRED = (
    "baseCurrencyOrderNotionalPresent", "baseCurrencyOrderNotional",
    "snapshotComplete", "snapshotObservedAtMs", "nowMs", "currentGrossNotional",
    "pendingBuyNotional", "pendingSellNotional", "realizedPnl", "unrealizedPnl",
    "peakEquity", "currentEquity",
)


class RetiredRiskApiTests(unittest.TestCase):
    def compile(self, body):
        return subprocess.run(
            shlex.split(os.environ.get("CXX", "c++")) +
            ["-std=c++11", "-fsyntax-only", "-x", "c++", "-I", str(ROOT), "-"],
            input='#include "HeptaTrade/risk/pre_trade_risk_engine.h"\n' + body,
            text=True, capture_output=True, timeout=15,
        )

    def test_authoritative_api_remains_readable_and_writable(self):
        result = self.compile('''int main() {
            PreTradeRiskContext context;
            context.authoritativeSnapshot.identity.present = true;
            context.authoritativeSnapshot.identity.generation = 1;
            context.authoritativeSnapshot.exposure.currentGrossNotional = 10.0;
            context.orderNotionalEvidence.baseCurrencyNotional = 2.0;
            return context.authoritativeSnapshot.identity.present ? 0 : 1;
        }''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_assignment_api_is_not_silently_accepted(self):
        for member in RETIRED:
            with self.subTest(member=member):
                result = self.compile(f"int main() {{ PreTradeRiskContext c; c.{member} = 1; }}")
                self.assertNotEqual(result.returncode, 0, member)


if __name__ == "__main__":
    unittest.main()
