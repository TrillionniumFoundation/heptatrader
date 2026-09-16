"""Compile the actual in-process result contract; no broker or source-token gate.

The native coordinator suite separately tests durable classification, duplicate
identity and invalid classifications across real journal replay.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CPP = r'''
#include "HeptaTrade/execution/venue_place_result.h"
#include <cassert>
#include <string>
int main(int argc, char** argv) {
    assert(argc == 2);
    const int scenario = std::stoi(argv[1]);
    const VenuePlaceRejection codes[] = {
        VenuePlaceRejection::Generic,
        VenuePlaceRejection::KillSwitchEngaged,
        VenuePlaceRejection::PostFillRefreshPending,
        VenuePlaceRejection::KillSwitchUncertain,
        VenuePlaceRejection::QuoteBindingRequired,
        VenuePlaceRejection::ContractMismatch,
        VenuePlaceRejection::QuoteChangedBeforeSend};
    const char* const expected[] = {
        "IB_PLACE_REJECT", "IB_PAPER_KILL_SWITCH_ENGAGED",
        "IB_POST_FILL_RISK_REFRESH_PENDING", "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN",
        "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED", "IB_PAPER_PLACE_CONTRACT_MISMATCH",
        "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND"};
    if (scenario == 0) {
        for (int i = 0; i < 7; ++i) {
            assert(std::string(VenuePlaceRejectionCode(codes[i])) == expected[i]);
            const auto result = VenuePlaceResult::Rejected(expected[i], 17);
            assert(result.rejection == codes[i] && result.orderId == 17);
            assert(result.detail == expected[i]);
        }
    } else if (scenario == 1) {
        for (int i = 0; i < 7; ++i) {
            auto result = VenuePlaceResult::Rejected(expected[i]);
            result.detail = "IB_PAPER_PLACE_CONTRACT_MISMATCH: explanatory suffix";
            assert(result.rejection == codes[i]);
            assert(std::string(VenuePlaceRejectionCode(result.rejection)) == expected[i]);
        }
    } else if (scenario == 2) {
        for (const auto code : codes) {
            auto result = VenuePlaceResult::Rejected(code, "localized diagnostic", 123);
            assert(result.disposition == VenuePlaceDisposition::Rejected);
            assert(result.rejection == code && result.orderId == 123);
            assert(result.detail == "localized diagnostic");
        }
    } else if (scenario == 3) {
        assert(VenuePlaceRejectionCode(static_cast<VenuePlaceRejection>(777)) == nullptr);
        for (const auto text : {"", "custom adapter refusal", "IB_PAPER_KILL_SWITCH_ENGAGED suffix"})
            assert(VenuePlaceResult::Rejected(text).rejection == VenuePlaceRejection::Generic);
        assert(VenuePlaceResult().disposition == VenuePlaceDisposition::Uncertain);
        assert(VenuePlaceResult::Uncertain("after possible send").disposition == VenuePlaceDisposition::Uncertain);
    } else { assert(false); }
}
'''


class VenuePlaceRejectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="hepta-place-rejection-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        source, cls.binary = root / "contract.cpp", root / "contract"
        source.write_text(CPP, encoding="utf-8")
        compiler = shlex.split(os.environ.get("CXX", "g++"))
        if not compiler:
            raise ValueError("CXX must name a compiler")
        subprocess.run(compiler + ["-std=c++11", "-Wall", "-Wextra", "-Werror",
                       "-I", str(ROOT), str(source), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True, timeout=60)

    def run_case(self, scenario):
        subprocess.run([str(self.binary), str(scenario)], check=True,
                       capture_output=True, text=True, timeout=5)

    def test_legacy_wire_codes_remain_identical(self):
        self.run_case(0)

    def test_description_cannot_reclassify_an_outcome(self):
        self.run_case(1)

    def test_typed_factory_separates_classification_and_description(self):
        self.run_case(2)

    def test_invalid_classification_and_unknown_diagnostics(self):
        self.run_case(3)


if __name__ == "__main__":
    unittest.main()
