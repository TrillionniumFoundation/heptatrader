"""Execute the production timing/JSON helper with explicit steady-clock points.

The compiler test has no Broker, credentials, scheduler sleeps or daemon mock.
Existing native/process suites own integration with the coordinator itself.
"""
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
#include "HeptaTrade/execution/execution_runtime_observation.h"
#include <cassert>
#include <chrono>
#include <iostream>
int main() {
    ExecutionRuntimeObservation execution;
    execution.present = true;
    execution.startupTimingPresent = true;
    execution.simulatorStateRecoveryLatency.Observe(2000000);
    execution.startupReadyLatency.Observe(9000000);
    using Clock = ExecutionOperationTiming::Clock;
    const auto start = Clock::time_point{};
    const auto acquired = start + std::chrono::nanoseconds(5000000);
    const auto end = acquired + std::chrono::nanoseconds(3000000);
    auto& operation = execution.operations[0];
    {
        ExecutionOperationTiming timer(operation, start, acquired);
        operation.Observe(ExecutionCommandStatus::Rejected);
        timer.Finish(end);
        timer.Finish(end + std::chrono::seconds(10)); // exact-once measurement
    }
    assert(operation.timingPresent);
    assert(operation.lockWait.samples == 1 && operation.lockWait.totalNs == 5000000);
    assert(operation.latency.samples == 1 && operation.latency.totalNs == 3000000);
    assert(operation.outsideLock.samples == 1 && operation.outsideLock.totalNs == 0);
    assert(operation.totalLatency.samples == 1 && operation.totalLatency.totalNs == 8000000);
    assert(!execution.operations[1].timingPresent);
    // Destruction during exception unwinding records both scopes once.
    try {
        const auto enteredException = Clock::now();
        const auto acquiredException = Clock::now();
        ExecutionOperationTiming timer(execution.operations[2], enteredException, acquiredException);
        execution.operations[2].Observe(4U);
        throw 7;
    } catch (int) {}
    assert(execution.operations[2].latency.samples == 1);
    assert(execution.operations[2].totalLatency.samples == 1);
    OmsJournalHealthSnapshot journal;
    journal.maxPendingBytes = 1048576;
    journal.maxPendingRecords = 1024;
    journal.replayMaxBytes = 67108864;
    journal.replayMaxRecords = 65536;
    journal.replayMaxRecordBytes = 262144;
    std::cout << ExecutionCapacityObservation(journal, execution, 1000, "test-epoch", 1000);
}
'''


class ExecutionLatencyBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="hepta-execution-timing-")
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        code, binary = root / "test.cpp", root / "test"
        code.write_text(CPP)
        subprocess.run(["g++", "-std=c++11", "-pthread", "-I", str(ROOT), str(code), "-o", str(binary)],
                       check=True, capture_output=True, text=True, timeout=60)
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
        cls.sample = json.loads(result.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_native_timing_and_cross_language_export(self):
        sample = copy.deepcopy(self.sample)
        report.validate_execution(sample["execution_metrics"])
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertIn("hepta_execution_place_latency_lock_wait_seconds_sum 0.005", text)
        self.assertIn("hepta_execution_place_latency_outside_lock_seconds_sum 0.0", text)
        self.assertIn("hepta_execution_place_latency_total_seconds_sum 0.008", text)
        self.assertIn('hepta_execution_operation_timing_present{operation="cancel"} 0', text)
        self.assertNotIn("hepta_execution_cancel_latency_total_seconds", text)
        self.assertIn("hepta_execution_startup_timing_present 1", text)
        self.assertIn("hepta_execution_simulator_state_recovery_latency_seconds_sum 0.002", text)
        self.assertIn("hepta_execution_startup_ready_latency_seconds_sum 0.009", text)

    def test_old_producer_is_not_rewritten_as_zero_wait(self):
        sample = copy.deepcopy(self.sample)
        for name in report.EXECUTION_TIMING_EXTENSION + report.EXECUTION_STARTUP_LATENCIES:
            sample["execution_metrics"].pop(name, None)
        report.validate_execution(sample["execution_metrics"])
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertNotIn("_lock_wait_seconds", text)
        self.assertNotIn("_total_seconds", text)
        self.assertIn("hepta_execution_startup_timing_present 0", text)

    def test_incomplete_or_inconsistent_new_timing_fails_closed(self):
        metrics = self.sample["execution_metrics"]
        for name in ("place_latency_lock_wait", "place_latency_total"):
            changed = copy.deepcopy(metrics)
            del changed[name]
            with self.subTest(name=name), self.assertRaises(ValueError):
                report.validate_execution(changed)
        changed = copy.deepcopy(metrics)
        changed["place_latency_outside_lock"]["total_ns"] += 100
        changed["place_latency_outside_lock"]["max_ns"] += 100
        changed["place_latency_outside_lock"]["last_ns"] += 100
        changed["place_latency_total"]["total_ns"] += 50
        with self.assertRaises(ValueError):
            report.validate_execution(changed)

    def test_saturated_measurement_is_not_exported_as_valid_histogram(self):
        sample = copy.deepcopy(self.sample)
        sample["execution_metrics"]["place_latency_total"]["saturated"] = True
        report.validate_execution(sample["execution_metrics"])
        text = report.prometheus(sample, report.report([sample], 1000))
        self.assertNotIn("hepta_execution_place_latency_total_seconds_bucket", text)
