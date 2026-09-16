"""Run native reason counters through the installed report's actual parser/exporter."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_report as report

CPP = r'''
#include "HeptaTrade/execution/execution_coordinator.h"
#include <cassert>
#include <chrono>
#include <iostream>
#include <limits>
#include <type_traits>

// This is compiler evidence that the obsolete callback is not an assignable API,
// not a source-spelling assertion. The supported typed callback remains usable.
template<class T> auto HasLastError(int) -> decltype(&T::lastIbRejectReason, std::true_type{});
template<class> std::false_type HasLastError(...);
static_assert(!decltype(HasLastError<ExecutionCoordinatorCallbacks>(0))::value,
              "coordinator must not read mutable last-error state");
int main() {
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.flattenOrder = [](const AuthoritativeFlattenPlan&, const std::string&) {
        return VenueFlattenResult::Submitted(42);
    };
    ExecutionRuntimeObservation execution; execution.present = true;
    auto& op = execution.operations[0];
    for (const auto reason : {"OMS_NEW_ENTRY_CAPACITY_EXHAUSTED", "IDEMPOTENCY_KEY_CONFLICT",
                             "PRIVATE_TOKEN_OR_ACCOUNT_MUST_NOT_BE_A_LABEL", ""}) {
        const auto start = ExecutionOperationTiming::Clock::time_point{};
        ExecutionOperationTiming timer(op, start, start);
        ExecutionCommandResult result; result.status = ExecutionCommandStatus::Rejected;
        result.reasonCode = reason; op.Observe(result);
        timer.Finish(start + std::chrono::nanoseconds(5));
    }
    const auto start = ExecutionOperationTiming::Clock::time_point{};
    { ExecutionOperationTiming timer(execution.operations[2], start, start);
      execution.operations[2].Observe(4U); timer.Finish(start); }
    assert(op.reasonCounts[ExecutionReasonIndex("IDEMPOTENCY_KEY_CONFLICT")] == 1);
    assert(op.reasonCounts[0] == 1 && op.reasonCounts[2] == 1);
    assert(execution.operations[2].reasonCounts[1] == 1);
    ExecutionOperationObservation saturated;
    saturated.reasonCounts[0] = std::numeric_limits<std::uint64_t>::max();
    saturated.Observe(ExecutionCommandStatus::Accepted);
    assert(saturated.saturated && saturated.reasonCounts[0] == std::numeric_limits<std::uint64_t>::max());
    ExecutionOperationObservation invalid; invalid.Observe(std::size_t(99)); assert(invalid.saturated);
    OmsJournalHealthSnapshot journal; journal.maxPendingBytes = 1048576;
    journal.maxPendingRecords = 1024; journal.replayMaxBytes = 67108864;
    journal.replayMaxRecords = 65536; journal.replayMaxRecordBytes = 262144;
    std::cout << ExecutionCapacityObservation(journal, execution, 1000, "reason-fixture", 1000) << '\n';
    for (const auto name : ExecutionReasonNames()) std::cout << name << '\n';
}
'''


class ExecutionReasonMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="hepta-reason-metrics-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        source, binary = root / "probe.cpp", root / "probe"
        source.write_text(CPP)
        subprocess.run(["g++", "-std=c++11", "-pthread", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT), str(source), "-o", str(binary)],
                       capture_output=True, text=True, check=True, timeout=60)
        result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=5)
        lines = result.stdout.splitlines()
        cls.sample, cls.names = json.loads(lines[0]), tuple(lines[1:])

    def test_cross_language_inventory_and_exact_accounting(self):
        self.assertEqual(self.names, report.EXECUTION_REASONS)
        metrics = copy.deepcopy(self.sample["execution_metrics"])
        report.validate_execution(metrics)
        self.assertEqual(sum(metrics["reason_counts"][0]), 4)
        self.assertEqual(metrics["reason_counts"][2][1], 1)

    def test_real_report_has_bounded_labels_and_no_payload(self):
        sample = copy.deepcopy(self.sample)
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertIn('hepta_execution_command_reasons_total{operation="place",reason="IDEMPOTENCY_KEY_CONFLICT"} 1', text)
        self.assertIn('hepta_execution_command_reasons_total{operation="place",reason="OTHER"} 1', text)
        self.assertIn('hepta_execution_command_reasons_total{operation="flatten",reason="EXCEPTION"} 1', text)
        self.assertNotIn("PRIVATE_TOKEN_OR_ACCOUNT", text)
        self.assertIn("hepta_execution_reason_metrics_present 1", text)

    def test_old_producer_absence_is_not_observed_zero(self):
        sample = copy.deepcopy(self.sample)
        for name in ("reason_counts", "reason_schema_version"):
            sample["execution_metrics"].pop(name)
        report.validate_execution(sample["execution_metrics"])
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertIn("hepta_execution_reason_metrics_present 0", text)
        self.assertNotIn("hepta_execution_command_reasons_total", text)

    def test_partial_wrong_version_bad_count_and_accounting_reject(self):
        mutations = [
            lambda m: m.pop("reason_schema_version"),
            lambda m: m.pop("reason_counts"),
            lambda m: m.update(reason_schema_version=True),
            lambda m: m.update(reason_schema_version=2),
            lambda m: m["reason_counts"].pop(),
            lambda m: m["reason_counts"][0].pop(),
            lambda m: m["reason_counts"][0].__setitem__(0, -1),
            lambda m: m["reason_counts"][0].__setitem__(0, True),
            lambda m: m["reason_counts"][0].__setitem__(0, 1.5),
            lambda m: m["reason_counts"][0].__setitem__(0, 2**64),
            lambda m: m["reason_counts"][0].__setitem__(0, 999),
        ]
        for mutation in mutations:
            metrics = copy.deepcopy(self.sample["execution_metrics"])
            mutation(metrics)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                report.validate_execution(metrics)

    def test_saturated_reasons_are_not_exported_as_valid_counters(self):
        sample = copy.deepcopy(self.sample)
        sample["execution_metrics"]["metrics_saturated"] = True
        report.validate_execution(sample["execution_metrics"])
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertIn("hepta_execution_reason_metrics_present 1", text)
        self.assertNotIn("hepta_execution_command_reasons_total", text)


if __name__ == "__main__":
    unittest.main()
