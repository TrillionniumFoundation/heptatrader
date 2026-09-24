#!/usr/bin/env python3
"""Validate observations from the existing real-service CTest; no timing SLO.

This is a test-report consumer, not an authority, a broker benchmark, or an
attestation. It never launches a trading service or invents missing samples.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

MARKER = "NATIVE_EXECUTION_LATENCY_JSON="
TEST = "hepta_research_native_execution_latency_tests"
MAX_LOG = 32 * 1024 * 1024
MAX_RECORD = 64 * 1024
PHASES = (
    "preview_ns", "place_persist_ns", "submit_stored_ns", "place_pipeline_ns",
    "status_ns", "cancel_persist_ns", "cancel_stored_ns",
    "cancel_terminal_observed_ns", "duplicate_ns",
)
FIXED = {
    "schema": "heptatrader.native-execution-latency.v1",
    "unit": "ns", "clock": "steady_clock", "concurrency": 1,
    "venue": "SIMULATOR", "execution_process": "separate_exec",
    "gateway_process": "client_process_threads", "broker_authorized": False,
    "different_uid_isolation": False, "cold_start_included": False,
    "fixture_count": 8, "warmup_per_fixture": 1, "measured_per_fixture": 3,
    "warmup_cycles": 8, "measured_cycles": 24,
    "place_send_attempts": 32, "cancel_send_attempts": 32,
    "max_send_attempts_per_command": 1, "final_position": 0,
    "final_active_orders": 0,
}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def parse_record(text: str) -> dict:
    require(len(text.encode("utf-8")) <= MAX_RECORD, "latency record too large")
    record = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    require(type(record) is dict, "record is not an object")
    require(set(record) == set(FIXED) | {"audit_records", "samples"}, "record schema mismatch")
    for key, expected in FIXED.items():
        require(type(record[key]) is type(expected) and record[key] == expected,
                f"invalid observation contract: {key}")
    require(type(record["audit_records"]) is int and 0 < record["audit_records"] < 2**64,
            "missing audit verification")
    rows = record["samples"]
    require(type(rows) is list and len(rows) == FIXED["measured_cycles"], "sample count mismatch")
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) == set(PHASES) | {"fixture_index", "cycle_index"},
                "sample schema mismatch")
        for key, expected in (("fixture_index", index // 3), ("cycle_index", 1 + index % 3)):
            require(type(row[key]) is int and row[key] == expected, "missing/reordered trial identity")
        for key in PHASES:
            require(type(row[key]) is int and 0 <= row[key] < 2**63, f"invalid duration: {key}")
        require(row["place_pipeline_ns"] >= sum(row[k] for k in PHASES[:3]),
                "pipeline shorter than measured calls")
        require(row["cancel_terminal_observed_ns"] >= row["cancel_stored_ns"],
                "terminal observation predates cancel response")
    return record


def extract_record(log: str) -> tuple[dict, str]:
    require(len(log.encode("utf-8")) <= MAX_LOG, "CTest log too large")
    records = [line[len(MARKER):] for line in log.splitlines() if line.startswith(MARKER)]
    require(len(records) == 1, "expected exactly one latency record; refusing missing or repeated runs")
    return parse_record(records[0]), records[0]


def validate_junit(xml: str, raw_record: str) -> None:
    require(len(xml.encode("utf-8")) <= MAX_LOG, "JUnit too large")
    require("<!DOCTYPE" not in xml.upper() and "<!ENTITY" not in xml.upper(), "unsupported XML declarations")
    root = ET.fromstring(xml)
    cases = list(root.iter("testcase"))
    require(bool(cases), "no CTest cases")
    require(all(c.find("failure") is None and c.find("error") is None and
                c.find("skipped") is None and c.get("status") in (None, "run") for c in cases),
            "JUnit contains failed, unrun or skipped tests")
    matches = [case for case in cases if case.get("name") == TEST]
    require(len(matches) == 1, "expected one real-service CTest case")
    output = matches[0].findtext("system-out", "")
    _, junit_record = extract_record(output)
    require(junit_record == raw_record, "CTest log and JUnit observations differ")


def summarize(record: dict) -> dict:
    result = {}
    for phase in PHASES:
        values = sorted(row[phase] for row in record["samples"])
        # Exact integer nearest rank: ceil(p*n/100)-1. No percentile interpolation.
        result[phase] = {"count": len(values), "min": values[0], "max": values[-1]}
        result[phase].update({f"p{p}": values[(p * len(values) + 99) // 100 - 1]
                              for p in (50, 95, 99)})
    return result


def bounded_read(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_LOG + 1)
    require(len(data) <= MAX_LOG, f"input too large: {path.name}")
    return data.decode("utf-8")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, timeout=15,
                          capture_output=True, text=True).stdout.strip()


def export(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parents[2]
    require(bool(re.fullmatch(r"[0-9a-f]{40}", args.source_sha)), "expected full Git SHA")
    require(git(root, "rev-parse", "HEAD") == args.source_sha, "source SHA is not local HEAD")
    require(not git(root, "status", "--porcelain", "--untracked-files=normal"), "source is not clean")
    log = bounded_read(args.log)
    xml = bounded_read(args.junit)
    record, raw = extract_record(log)
    validate_junit(xml, raw)
    cache = bounded_read(args.build_dir / "CMakeCache.txt")
    def cache_value(key: str) -> str:
        match = re.search(rf"^{re.escape(key)}:[^=]*=(.*)$", cache, re.MULTILINE)
        require(match is not None, f"missing CMake setting: {key}")
        return match.group(1)
    require(cache_value("CMAKE_HOME_DIRECTORY") == str(root), "not the canonical root build")
    compiler = cache_value("CMAKE_CXX_COMPILER")
    version = subprocess.run([compiler, "--version"], check=True, timeout=10,
                             capture_output=True, text=True).stdout.splitlines()[0]
    report = {
        "schema": "heptatrader.native-execution-latency-report.v1",
        "source_commit": args.source_sha, "source_tree": git(root, "rev-parse", "HEAD^{tree}"),
        "source_identity_note": "Local reconstructed commits are not remote commit identities.",
        "input_sha256": {"ctest_log": hashlib.sha256(log.encode()).hexdigest(),
                         "junit": hashlib.sha256(xml.encode()).hexdigest(),
                         "record": hashlib.sha256(raw.encode()).hexdigest()},
        "environment": {"system": platform.system(), "release": platform.release(),
                        "machine": platform.machine(), "logical_cpus": os.cpu_count(),
                        "compiler": version, "build_type": cache_value("CMAKE_BUILD_TYPE"),
                        "cxx_flags": cache_value("CMAKE_CXX_FLAGS"),
                        "configuration_flags": cache_value("CMAKE_CXX_FLAGS_" + cache_value("CMAKE_BUILD_TYPE").upper())},
        "interpretation": "Eight independent fixtures, each one warmup and three measured serial cycles. "
            "Unchanged four-cancel/session/minute limit. Small observed sample, not steady-state "
            "throughput, tail-confidence, broker latency, different-UID qualification, or an SLO pass. "
            "Cancellation-terminal time includes fixture observation IPC and 2ms polling.",
        "percentile_method": "nearest_rank_integer_ns", "observations": record,
        "summary_ns": summarize(record),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Do not follow or replace an existing file. Failed runs cannot silently
    # overwrite a previously accepted report; use a fresh per-run output path.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Validated {len(record['samples'])} serial cycles: {args.output}")


class ReportTests(unittest.TestCase):
    def fixture(self) -> dict:
        data = dict(FIXED, audit_records=128, samples=[])
        for i in range(24):
            row = dict.fromkeys(PHASES, i + 1)
            row.update(fixture_index=i // 3, cycle_index=1 + i % 3, place_pipeline_ns=3*(i+1))
            data["samples"].append(row)
        return data

    def test_exact_percentiles(self) -> None:
        data = parse_record(json.dumps(self.fixture()))
        self.assertEqual(summarize(data)["preview_ns"],
                         {"count": 24, "min": 1, "max": 24, "p50": 12, "p95": 23, "p99": 24})

    def test_contract_mutations(self) -> None:
        for field in FIXED:
            for value in (None, [], {}, "invalid"):
                data = self.fixture(); data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_record(json.dumps(data))
        for value in (True, 1.0, -1, 0, 2**64):
            data = self.fixture(); data["audit_records"] = value
            with self.assertRaises(ValueError): parse_record(json.dumps(data))

    def test_durations_and_identities(self) -> None:
        for field in (*PHASES, "fixture_index", "cycle_index"):
            for value in (True, 1.25, -1, 2**63, None):
                data = self.fixture(); data["samples"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_record(json.dumps(data))
        for field in ("place_pipeline_ns", "cancel_terminal_observed_ns"):
            data = self.fixture(); data["samples"][0][field] = 0
            with self.assertRaises(ValueError): parse_record(json.dumps(data))
        data = self.fixture(); data["samples"].reverse()
        with self.assertRaises(ValueError): parse_record(json.dumps(data))

    def test_duplicate_unknown_missing(self) -> None:
        raw = json.dumps(self.fixture())
        for invalid in (raw[:-1] + ',"audit_records":128}',
                        raw.replace('"preview_ns": 1,', '"preview_ns": 1,"preview_ns": 1,', 1)):
            with self.assertRaises(ValueError): parse_record(invalid)
        for edit in (lambda d: d.update(extra=1), lambda d: d.pop("unit"),
                     lambda d: d["samples"].pop(), lambda d: d["samples"].append(d["samples"][0]),
                     lambda d: d["samples"][0].update(extra=1)):
            data = self.fixture(); edit(data)
            with self.assertRaises(ValueError): parse_record(json.dumps(data))

    def test_truncation_and_constants(self) -> None:
        raw = json.dumps(self.fixture())
        for end in (0, 1, len(raw)//2, len(raw)-1):
            with self.assertRaises(ValueError): parse_record(raw[:end])
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(ValueError): parse_record(raw.replace('"audit_records": 128',
                                                                          '"audit_records": '+constant))
        with self.assertRaises(ValueError): parse_record(" "*(MAX_RECORD+1))

    def test_log_and_junit_binding(self) -> None:
        raw = json.dumps(self.fixture()); line = MARKER + raw
        root = ET.Element("testsuite"); case = ET.SubElement(root, "testcase", name=TEST, status="run")
        ET.SubElement(case, "system-out").text = "CTEST_FULL_OUTPUT\n" + line
        validate_junit(ET.tostring(root, encoding="unicode"), raw)
        self.assertEqual(extract_record("noise\n"+line+"\n")[0], self.fixture())
        for log in ("", line+"\n"+line):
            with self.assertRaises(ValueError): extract_record(log)
        for tag in ("failure", "error", "skipped"):
            failed = copy.deepcopy(root); ET.SubElement(failed[0], tag)
            with self.assertRaises(ValueError): validate_junit(ET.tostring(failed, encoding="unicode"), raw)
        with self.assertRaises(ValueError): validate_junit(ET.tostring(root, encoding="unicode"), raw+" ")
        with self.assertRaises(ValueError): validate_junit("<!DOCTYPE x><testsuite/>", raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReportTests))
        return 0 if result.wasSuccessful() else 1
    if not all((args.log, args.junit, args.build_dir, args.source_sha, args.output)):
        parser.error("export requires --log, --junit, --build-dir, --source-sha and --output")
    try:
        export(args)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, ET.ParseError) as error:
        print(f"latency report rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
