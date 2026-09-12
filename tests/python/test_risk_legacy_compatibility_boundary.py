#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "HeptaTrade/risk/pre_trade_risk_engine.h"
IMPLEMENTATION = ROOT / "HeptaTrade/risk/pre_trade_risk_engine.cpp"

LEGACY_FIELDS = (
    "baseCurrencyOrderNotionalPresent",
    "baseCurrencyOrderNotional",
    "snapshotComplete",
    "snapshotObservedAtMs",
    "nowMs",
    "currentGrossNotional",
    "pendingBuyNotional",
    "pendingSellNotional",
    "realizedPnl",
    "unrealizedPnl",
    "peakEquity",
    "currentEquity",
)


class RiskLegacyCompatibilityBoundaryTests(unittest.TestCase):
    def _compiler(self) -> str:
        requested = os.environ.get("CXX")
        candidates = [requested] if requested else []
        candidates.extend(["c++", "g++", "clang++"])
        for candidate in candidates:
            if candidate:
                resolved = shutil.which(candidate)
                if resolved:
                    return resolved
        self.fail("a C++ compiler is required to validate the legacy risk type boundary")

    def _compile(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="hepta-risk-legacy-") as directory:
            path = Path(directory) / "probe.cpp"
            path.write_text(source, encoding="utf-8")
            return subprocess.run(
                [
                    self._compiler(),
                    "-std=c++11",
                    "-fsyntax-only",
                    "-I",
                    str(ROOT / "HeptaTrade"),
                    str(path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def test_removed_unbound_scalar_writes_fail_to_compile(self) -> None:
        for field in LEGACY_FIELDS:
            with self.subTest(field=field):
                result=self._compile('#include "risk/pre_trade_risk_engine.h"\n'
                    'void Bad(PreTradeRiskContext& ctx) { ctx.'+field+' = 1; }\n')
                self.assertNotEqual(result.returncode,0,'removed scalar compatibility unexpectedly restored')

    def test_authoritative_evidence_reads_compile(self) -> None:
        result=self._compile('''#include "risk/pre_trade_risk_engine.h"
        double Valid(const PreTradeRiskContext& ctx) {
            return ctx.authoritativeSnapshot.exposure.currentGrossNotional
                + ctx.orderNotionalEvidence.baseCurrencyNotional;
        }''')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_alias_consumption_is_rejected_by_compiler(self) -> None:
        source = r'''
#include "risk/pre_trade_risk_engine.h"
double Bad(PreTradeRiskContext& context) {
    const auto& alias = context;
    return alias.currentGrossNotional;
}
'''
        result = self._compile(source)
        self.assertNotEqual(result.returncode, 0, "legacy alias unexpectedly converted to double")

    def test_pointer_and_parenthesized_dereference_consumption_is_rejected(self) -> None:
        sources = (
            r'''
#include "risk/pre_trade_risk_engine.h"
double Bad(PreTradeRiskContext* ctx) { return ctx->pendingSellNotional; }
''',
            r'''
#include "risk/pre_trade_risk_engine.h"
double Bad(PreTradeRiskContext* ctx) { return (*ctx).pendingBuyNotional; }
''',
        )
        for source in sources:
            with self.subTest(source=source):
                result = self._compile(source)
                self.assertNotEqual(result.returncode, 0, "legacy pointer access unexpectedly converted")

    def test_helper_template_indirection_cannot_consume_legacy_value(self) -> None:
        source = r'''
#include "risk/pre_trade_risk_engine.h"
template <typename T>
auto Legacy(const T& value) -> decltype(value.realizedPnl) { return value.realizedPnl; }
double Bad(const PreTradeRiskContext& ctx) { return Legacy(ctx); }
'''
        result = self._compile(source)
        self.assertNotEqual(result.returncode, 0, "template indirection unexpectedly converted legacy value")

    def test_pointer_to_member_indirection_cannot_consume_legacy_value(self) -> None:
        source = r'''
#include "risk/pre_trade_risk_engine.h"
double Bad(const PreTradeRiskContext& ctx) {
    auto member = &PreTradeRiskContext::currentEquity;
    return ctx.*member;
}
'''
        result = self._compile(source)
        self.assertNotEqual(result.returncode, 0, "pointer-to-member unexpectedly converted legacy value")

    def test_boolean_legacy_authority_cannot_drive_control_flow(self) -> None:
        source = r'''
#include "risk/pre_trade_risk_engine.h"
bool Bad(const PreTradeRiskContext& ctx) {
    return ctx.baseCurrencyOrderNotionalPresent && ctx.snapshotComplete;
}
'''
        result = self._compile(source)
        self.assertNotEqual(result.returncode, 0, "legacy bool wrappers unexpectedly drove control flow")

    def test_authoritative_types_remain_the_documented_path(self) -> None:
        header = HEADER.read_text(encoding="utf-8")
        self.assertIn("PreTradeRiskOrderNotionalEvidence orderNotionalEvidence", header)
        self.assertIn("PreTradeRiskAuthoritativeSnapshot authoritativeSnapshot", header)
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("orderNotionalEvidence", implementation)
        self.assertIn("authoritativeSnapshot", implementation)


if __name__ == "__main__":
    unittest.main()
