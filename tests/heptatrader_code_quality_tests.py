#!/usr/bin/env python3

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import check_heptatrader_code_quality as quality  # noqa: E402


POLICY = REPOSITORY / "policies/heptatrader-code-quality-v1.json"
SECURITY_CRITICAL_NATIVE_BUDGETS = {
    "HeptaTrade/execution/execution_coordinator.cpp": {
        "lines": 1315,
        "maximum_function_lines": 194,
        "maximum_cyclomatic_complexity": 38,
        "maximum_cognitive_complexity": 41,
    },
    "HeptaTrade/execution/execution_coordinator_cancel.cpp": {
        "lines": 215,
        "maximum_function_lines": 194,
        "maximum_cyclomatic_complexity": 39,
        "maximum_cognitive_complexity": 41,
    },
    "HeptaTrade/execution/execution_coordinator_reconnect.cpp": {
        "lines": 152,
        "maximum_function_lines": 66,
        "maximum_cyclomatic_complexity": 19,
        "maximum_cognitive_complexity": 24,
    },
    "HeptaTrade/execution/execution_coordinator_recovery.cpp": {
        "lines": 105,
        "maximum_function_lines": 55,
        "maximum_cyclomatic_complexity": 12,
        "maximum_cognitive_complexity": 15,
    },
    "HeptaTrade/execution/execution_coordinator_terminal.cpp": {
        "lines": 48,
        "maximum_function_lines": 48,
        "maximum_cyclomatic_complexity": 10,
        "maximum_cognitive_complexity": 9,
    },
    "HeptaTrade/execution/unix_execution_service.cpp": {
        "lines": 1087,
        "maximum_function_lines": 110,
        "maximum_cyclomatic_complexity": 19,
        "maximum_cognitive_complexity": 23,
    },
    "HeptaTrade/tool_host/typed_tool_protocol.cpp": {
        "lines": 507,
        "maximum_function_lines": 64,
        "maximum_cyclomatic_complexity": 35,
        "maximum_cognitive_complexity": 34,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp": {
        "lines": 1530,
        "maximum_function_lines": 144,
        "maximum_cyclomatic_complexity": 43,
        "maximum_cognitive_complexity": 50,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp": {
        "lines": 1143,
        "maximum_function_lines": 80,
        "maximum_cyclomatic_complexity": 35,
        "maximum_cognitive_complexity": 40,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp": {
        "lines": 400,
        "maximum_function_lines": 80,
        "maximum_cyclomatic_complexity": 25,
        "maximum_cognitive_complexity": 25,
    },
    "HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp": {
        "lines": 98,
        "maximum_function_lines": 93,
        "maximum_cyclomatic_complexity": 25,
        "maximum_cognitive_complexity": 24,
    },
    "HeptaTrade/execution/execution_authoritative_flatten.cpp": {
        "lines": 485,
        "maximum_function_lines": 291,
        "maximum_cyclomatic_complexity": 42,
        "maximum_cognitive_complexity": 55,
    },
    "HeptaTrade/execution/execution_authoritative_flatten_dispatch.cpp": {
        "lines": 358,
        "maximum_function_lines": 153,
        "maximum_cyclomatic_complexity": 23,
        "maximum_cognitive_complexity": 31,
    },
    "HeptaTrade/execution/execution_place_order_dispatch.cpp": {
        "lines": 280,
        "maximum_function_lines": 139,
        "maximum_cyclomatic_complexity": 22,
        "maximum_cognitive_complexity": 28,
    },
    "HeptaTrade/execution/execution_service_runtime_composition.cpp": {
        "lines": 875,
        "maximum_function_lines": 165,
        "maximum_cyclomatic_complexity": 20,
        "maximum_cognitive_complexity": 26,
    },
    "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp": {
        "lines": 319,
        "maximum_function_lines": 80,
        "maximum_cyclomatic_complexity": 17,
        "maximum_cognitive_complexity": 15,
    },
    "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp": {
        "lines": 190,
        "maximum_function_lines": 101,
        "maximum_cyclomatic_complexity": 30,
        "maximum_cognitive_complexity": 28,
    },
    "HeptaTrade/execution/ib_paper_execution_hook_authority.cpp": {
        "lines": 68,
        "maximum_function_lines": 17,
        "maximum_cyclomatic_complexity": 4,
        "maximum_cognitive_complexity": 3,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_broker.cpp": {
        "lines": 705,
        "maximum_function_lines": 230,
        "maximum_cyclomatic_complexity": 57,
        "maximum_cognitive_complexity": 73,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_composition.cpp": {
        "lines": 628,
        "maximum_function_lines": 61,
        "maximum_cyclomatic_complexity": 22,
        "maximum_cognitive_complexity": 23,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_events.cpp": {
        "lines": 943,
        "maximum_function_lines": 133,
        "maximum_cyclomatic_complexity": 59,
        "maximum_cognitive_complexity": 60,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_flatten.cpp": {
        "lines": 128,
        "maximum_function_lines": 56,
        "maximum_cyclomatic_complexity": 14,
        "maximum_cognitive_complexity": 12,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_policy.cpp": {
        "lines": 460,
        "maximum_function_lines": 194,
        "maximum_cyclomatic_complexity": 40,
        "maximum_cognitive_complexity": 47,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_recent_orders.cpp": {
        "lines": 220,
        "maximum_function_lines": 60,
        "maximum_cyclomatic_complexity": 17,
        "maximum_cognitive_complexity": 17,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_event_drain.cpp": {
        "lines": 150,
        "maximum_function_lines": 120,
        "maximum_cyclomatic_complexity": 27,
        "maximum_cognitive_complexity": 48,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_quote_admission.cpp": {
        "lines": 600,
        "maximum_function_lines": 120,
        "maximum_cyclomatic_complexity": 27,
        "maximum_cognitive_complexity": 60,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_startup.cpp": {
        "lines": 238,
        "maximum_function_lines": 83,
        "maximum_cyclomatic_complexity": 33,
        "maximum_cognitive_complexity": 38,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_state.cpp": {
        "lines": 562,
        "maximum_function_lines": 172,
        "maximum_cyclomatic_complexity": 59,
        "maximum_cognitive_complexity": 68,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_terminal.cpp": {
        "lines": 319,
        "maximum_function_lines": 174,
        "maximum_cyclomatic_complexity": 53,
        "maximum_cognitive_complexity": 74,
    },
    "HeptaTrade/execution/ib_paper_execution_runtime_terminal_state.cpp": {
        "lines": 693,
        "maximum_function_lines": 290,
        "maximum_cyclomatic_complexity": 81,
        "maximum_cognitive_complexity": 89,
    },
    "HeptaTrade/execution/ib_paper_flatten_plan_binding.cpp": {
        "lines": 134,
        "maximum_function_lines": 47,
        "maximum_cyclomatic_complexity": 11,
        "maximum_cognitive_complexity": 10,
    },
    "HeptaTrade/execution/paper_terminal_external_latch.cpp": {
        "lines": 970,
        "maximum_function_lines": 153,
        "maximum_cyclomatic_complexity": 62,
        "maximum_cognitive_complexity": 61,
    },
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp": {
        "lines": 1476,
        "maximum_function_lines": 243,
        "maximum_cyclomatic_complexity": 63,
        "maximum_cognitive_complexity": 65,
    },
    "HeptaTrade/execution/unix_execution_service_flatten.cpp": {
        "lines": 82,
        "maximum_function_lines": 42,
        "maximum_cyclomatic_complexity": 6,
        "maximum_cognitive_complexity": 5,
    },
    "HeptaTrade/execution/unix_execution_service_flatten_client.cpp": {
        "lines": 78,
        "maximum_function_lines": 28,
        "maximum_cyclomatic_complexity": 5,
        "maximum_cognitive_complexity": 4,
    },
    "HeptaTrade/execution/unix_execution_service_flatten_permit.cpp": {
        "lines": 228,
        "maximum_function_lines": 77,
        "maximum_cyclomatic_complexity": 12,
        "maximum_cognitive_complexity": 14,
    },
    "HeptaTrade/tool_host/agent_os_runtime_composition.cpp": {
        "lines": 165,
        "maximum_function_lines": 45,
        "maximum_cyclomatic_complexity": 10,
        "maximum_cognitive_complexity": 10,
    },
    "HeptaTrade/tool_host/session_supervisor_audit_journal.cpp": {
        "lines": 700,
        "maximum_function_lines": 160,
        "maximum_cyclomatic_complexity": 45,
        "maximum_cognitive_complexity": 65,
    },
    "HeptaTrade/tool_host/tool_decision_audit.cpp": {
        "lines": 190,
        "maximum_function_lines": 50,
        "maximum_cyclomatic_complexity": 8,
        "maximum_cognitive_complexity": 8,
    },
    "HeptaTrade/tool_host/trading_tool_host.cpp": {
        "lines": 1208,
        "maximum_function_lines": 114,
        "maximum_cyclomatic_complexity": 40,
        "maximum_cognitive_complexity": 44,
    },
    "HeptaTrade/tool_host/trading_tool_session_lifecycle.cpp": {
        "lines": 547,
        "maximum_function_lines": 106,
        "maximum_cyclomatic_complexity": 25,
        "maximum_cognitive_complexity": 26,
    },
    "HeptaTrade/tool_host/trading_tool_watch_transaction.cpp": {
        "lines": 398,
        "maximum_function_lines": 77,
        "maximum_cyclomatic_complexity": 23,
        "maximum_cognitive_complexity": 27,
    },
    "HeptaTrade/tool_host/unix_tool_server.cpp": {
        "lines": 691,
        "maximum_function_lines": 168,
        "maximum_cyclomatic_complexity": 25,
        "maximum_cognitive_complexity": 28,
    },
}
MANUAL_QUARANTINE_FIXTURE = """name: manual-quarantine-fixture

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  quarantine:
    steps:
      - name: Preserve checkout credential boundary
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
        with:
          persist-credentials: false
      - name: Emit non-authority contract
        shell: pwsh
        env:
          HEPTA_REVIEWED_SHA: reviewed
        run: |
          $expected = $env:HEPTA_REVIEWED_SHA
          @(
            'release_eligible=false',
            'host_execution_authorized=false',
            'broker_connection_authorized=false',
            'paper_authorized=false',
            'live_authorized=false'
          ) | Out-Null
"""


class CodeQualityTests(unittest.TestCase):
    def _strict_source_excludes(self, relative: str) -> None:
        marker = REPOSITORY / ".hepta/source-bundle-manifest.json"
        self.assertTrue(
            marker.is_file(),
            f"{relative} is absent outside a strict source bundle")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest.get("schema"), "hepta.clean-source-bundle.v2")
        records = manifest.get("files")
        self.assertIsInstance(records, list)
        listed = {
            record.get("path")
            for record in records
            if isinstance(record, dict)
        }
        self.assertNotIn(relative, listed)

    def _audit_with_document(
            self, document: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps(document) + "\n", encoding="utf-8")
            path.chmod(0o600)
            return quality.audit(REPOSITORY, path)

    def _run_gateway_symbol_gate(
            self,
            directory: Path,
            symbols: list[str],
            *,
            enforce_budget: str | None) -> subprocess.CompletedProcess[str]:
        binary = directory / "hepta-tool-gatewayd"
        binary.write_bytes(b"fixture\n")
        binary.chmod(0o700)
        nm = directory / "fixture-nm"
        nm.write_text(
            "#!/usr/bin/env python3\n"
            f"symbols = {symbols!r}\n"
            "print('\\n'.join(symbols))\n",
            encoding="utf-8")
        nm.chmod(0o700)
        cmake = shutil.which("cmake")
        if cmake is None:
            self.fail("cmake is unavailable")
        command = [
            Path(cmake).resolve(strict=True).as_posix(),
            f"-DHEPTA_GATEWAY_BINARY={binary}",
            f"-DHEPTA_NM_EXECUTABLE={nm}",
        ]
        if enforce_budget is not None:
            command.append(
                "-DHEPTA_GATEWAY_ENFORCE_SYMBOL_BUDGET="
                f"{enforce_budget}")
        command.extend([
            "-P",
            str(REPOSITORY /
                "cmake/verify_gateway_forbidden_symbols.cmake"),
        ])
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20)

    def test_current_native_modules_respect_hard_budgets(self) -> None:
        report = quality.audit(REPOSITORY, POLICY)
        self.assertTrue(report["passed"], report["violations"])
        self.assertGreater(report["cmake_cpp_references"], 0)
        self.assertGreaterEqual(
            report["coverage_line_minimum_percent"], 70)
        self.assertFalse(report["coverage_toolchain_provisioned"])
        self.assertIn(
            "HeptaTrade/adapter_ib/ib_gateway_adapter.cpp",
            report["native_structure_metrics"])
        self.assertGreater(
            report["native_structure_metrics"][
                "HeptaTrade/execution/execution_coordinator.cpp"
            ]["function_count"],
            0)
        self.assertGreater(report["include_graph"]["edge_count"], 0)
        self.assertGreaterEqual(
            report["include_graph"]["maximum_local_fan_out"], 1)

    def test_security_critical_modules_have_reviewed_explicit_budgets(
            self) -> None:
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        for relative, expected in SECURITY_CRITICAL_NATIVE_BUDGETS.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    document["native_line_budgets"][relative],
                    expected["lines"])
                self.assertEqual(
                    document["native_structure_budgets"][relative],
                    {
                        key: value
                        for key, value in expected.items()
                        if key != "lines"
                    })
        self.assertEqual(
            document["include_graph"],
            {
                "maximum_edges": 80,
                "maximum_local_fan_in": 11,
                "maximum_local_fan_out": 8,
            })

    def test_execution_coordinator_split_is_budgeted_and_compiled(self) -> None:
        modules = {
            "execution_coordinator_cancel.cpp",
            "execution_coordinator_recovery.cpp",
            "execution_coordinator_reconnect.cpp",
            "execution_coordinator_terminal.cpp",
        }
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        product_cmake = (
            REPOSITORY / "HeptaTrade/CMakeLists.txt").read_text(
                encoding="utf-8")
        test_cmake = (REPOSITORY / "tests/CMakeLists.txt").read_text(
            encoding="utf-8")
        for name in modules:
            relative = "HeptaTrade/execution/" + name
            with self.subTest(relative=relative):
                self.assertIn(relative, document["native_line_budgets"])
                self.assertIn(relative, document["native_structure_budgets"])
                self.assertIn(relative, SECURITY_CRITICAL_NATIVE_BUDGETS)
                self.assertIn("execution/" + name, product_cmake)
                self.assertIn("../HeptaTrade/execution/" + name, test_cmake)

    def test_ib_paper_runtime_split_is_budgeted_and_compiled(self) -> None:
        modules = {
            "ib_paper_execution_runtime_event_drain.cpp",
            "ib_paper_execution_runtime_quote_admission.cpp",
            "ib_paper_execution_runtime_startup.cpp",
            "ib_paper_execution_runtime_state.cpp",
            "ib_paper_execution_runtime_events.cpp",
            "ib_paper_execution_runtime_broker.cpp",
            "ib_paper_execution_runtime_policy.cpp",
            "ib_paper_execution_runtime_recent_orders.cpp",
            "ib_paper_execution_runtime_terminal.cpp",
            "ib_paper_execution_runtime_terminal_state.cpp",
        }
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        product_cmake = (
            REPOSITORY / "HeptaTrade/CMakeLists.txt").read_text(
                encoding="utf-8")
        test_cmake = (REPOSITORY / "tests/CMakeLists.txt").read_text(
            encoding="utf-8")
        for name in modules:
            relative = "HeptaTrade/execution/" + name
            with self.subTest(relative=relative):
                self.assertIn(relative, document["native_line_budgets"])
                self.assertIn(relative, document["native_structure_budgets"])
                self.assertIn(relative, SECURITY_CRITICAL_NATIVE_BUDGETS)
                self.assertIn("execution/" + name, product_cmake)
                self.assertIn("../HeptaTrade/execution/" + name, test_cmake)
        self.assertGreaterEqual(
            test_cmake.count("${HEPTA_IB_PAPER_RUNTIME_SOURCES}"), 3)

    def test_paper_terminal_latches_are_budgeted_and_compiled(self) -> None:
        modules = {
            "paper_terminal_external_latch.cpp",
            "paper_terminal_mutation_manifest.cpp",
        }
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        product_cmake = (
            REPOSITORY / "HeptaTrade/CMakeLists.txt").read_text(
                encoding="utf-8")
        test_cmake = (REPOSITORY / "tests/CMakeLists.txt").read_text(
            encoding="utf-8")
        for name in modules:
            relative = "HeptaTrade/execution/" + name
            with self.subTest(relative=relative):
                self.assertIn(relative, document["native_line_budgets"])
                self.assertIn(relative, document["native_structure_budgets"])
                self.assertIn(relative, SECURITY_CRITICAL_NATIVE_BUDGETS)
                self.assertIn("execution/" + name, product_cmake)
                self.assertIn("../HeptaTrade/execution/" + name, test_cmake)

    def test_ib_gateway_adapter_split_is_budgeted_and_compiled(self) -> None:
        modules = {
            "ib_gateway_adapter_event_state.cpp",
            "ib_gateway_adapter_order_submission.cpp",
        }
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        product_cmake = (
            REPOSITORY / "HeptaTrade/CMakeLists.txt").read_text(
                encoding="utf-8")
        test_cmake = (REPOSITORY / "tests/CMakeLists.txt").read_text(
            encoding="utf-8")
        for name in modules:
            relative = "HeptaTrade/adapter_ib/" + name
            with self.subTest(relative=relative):
                self.assertIn(relative, document["native_line_budgets"])
                self.assertIn(relative, document["native_structure_budgets"])
                self.assertIn(relative, SECURITY_CRITICAL_NATIVE_BUDGETS)
                self.assertIn("adapter_ib/" + name, product_cmake)
                self.assertIn(
                    "../HeptaTrade/adapter_ib/" + name, test_cmake)
        self.assertGreaterEqual(
            product_cmake.count("${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}"), 2)
        self.assertGreaterEqual(
            test_cmake.count("${HEPTA_IB_GATEWAY_ADAPTER_SOURCES}"), 4)

    def test_security_critical_line_budget_rejects_growth(self) -> None:
        relative = "HeptaTrade/tool_host/tool_decision_audit.cpp"
        current = quality.audit(REPOSITORY, POLICY)
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        document["native_line_budgets"][relative] = (
            current["native_line_counts"][relative] - 1)
        report = self._audit_with_document(document)
        self.assertFalse(report["passed"])
        self.assertIn(
            f"{relative} has {current['native_line_counts'][relative]} lines",
            "\n".join(report["violations"]))

    def test_structure_budget_rejects_function_growth(self) -> None:
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        document["native_structure_budgets"][
            "HeptaTrade/execution/execution_coordinator.cpp"
        ]["maximum_function_lines"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps(document) + "\n", encoding="utf-8")
            path.chmod(0o600)
            report = quality.audit(REPOSITORY, path)
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("maximum function lines" in violation
                for violation in report["violations"]),
            report["violations"])

    def test_security_critical_complexity_budgets_reject_growth(self) -> None:
        relative = "HeptaTrade/tool_host/unix_tool_server.cpp"
        current = quality.audit(REPOSITORY, POLICY)
        metrics = current["native_structure_metrics"][relative]
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        limits = document["native_structure_budgets"][relative]
        limits["maximum_function_lines"] = (
            metrics["maximum_function"]["lines"] - 1)
        limits["maximum_cyclomatic_complexity"] = (
            metrics["maximum_cyclomatic_function"][
                "cyclomatic_complexity"] - 1)
        limits["maximum_cognitive_complexity"] = (
            metrics["maximum_cognitive_function"][
                "cognitive_complexity"] - 1)
        report = self._audit_with_document(document)
        violations = "\n".join(report["violations"])
        self.assertFalse(report["passed"])
        self.assertIn(f"{relative} maximum function lines", violations)
        self.assertIn(
            f"{relative} maximum cyclomatic complexity", violations)
        self.assertIn(
            f"{relative} maximum cognitive complexity", violations)

    def test_expanded_include_graph_budgets_reject_growth(self) -> None:
        current = quality.audit(REPOSITORY, POLICY)
        document = json.loads(POLICY.read_text(encoding="utf-8"))
        graph = current["include_graph"]
        document["include_graph"] = {
            "maximum_edges": graph["edge_count"] - 1,
            "maximum_local_fan_in": graph["maximum_local_fan_in"] - 1,
            "maximum_local_fan_out": graph["maximum_local_fan_out"] - 1,
        }
        report = self._audit_with_document(document)
        violations = "\n".join(report["violations"])
        self.assertFalse(report["passed"])
        self.assertIn("include graph edge count", violations)
        self.assertIn("include graph local fan-in", violations)
        self.assertIn("include graph local fan-out", violations)

    def test_lexical_complexity_is_deterministic_and_ignores_comments(
            self) -> None:
        source = """
int fixture(bool first, bool second) {
  // if (ignored && ignored) { }
  const char* text = "while (ignored || ignored) { }";
  if (first && second) {
    for (int index = 0; index != 2; ++index) {
      if (index == 1) {
        return index;
      }
    }
  }
  return 0;
}
"""
        regions = quality._function_regions(source)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["name"], "fixture")
        self.assertEqual(regions[0]["cyclomatic_complexity"], 5)
        self.assertEqual(regions[0]["cognitive_complexity"], 7)

    def test_lexical_function_scan_ignores_preprocessor_expressions_and_macros(
            self) -> None:
        source = """#if defined(__linux__)
#define GENERATED(value) { if (value) { return; } }
#elif defined(__APPLE__) \\
  && defined(OTHER_PLATFORM)
#define CONTINUED(value) { while (value) { value = false; } }
#endif
int fixture() { return 0; }
"""
        regions = quality._function_regions(source)
        self.assertEqual([region["name"] for region in regions], ["fixture"])
        # Directive blanking must preserve the source location of real code.
        self.assertEqual(regions[0]["start_line"], 7)

    def test_ci_reference_extractor_finds_prefixed_and_root_inputs(
            self) -> None:
        workflow = """
run: |
  python3 scripts/check_example.py --policy policies/example-v1.json
  python -m pytest -q tests/test_example.py
  python Scripts\\check_case_drift.py
  python scripts\\check_windows_path.py
  "$SOURCE/scripts/run_variable.py"
  "${SOURCE}/scripts/run_braced.py"
  bash -n status_legacy_example.sh
ref: ${{ github.event.pull_request.head.sha || github.sha }}
"""
        references = quality._ci_local_references(
            workflow,
            ["scripts", "tests", "policies"],
            [".py", ".ps1", ".sh"])
        self.assertEqual(
            references,
            [
                "Scripts/check_case_drift.py",
                "policies/example-v1.json",
                "scripts/check_example.py",
                "scripts/check_windows_path.py",
                "scripts/run_braced.py",
                "scripts/run_variable.py",
                "status_legacy_example.sh",
                "tests/test_example.py",
            ])

    def test_ci_checkout_contract_binds_all_jobs_to_reviewed_head(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        contract = policy["ci_archive"]["checkout_contract"]
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        report = quality._ci_checkout_contract(workflow, contract)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["checkout_count"], 8)
        self.assertEqual(report["fetch_depth_count"], 8)
        self.assertEqual(report["pull_request_head_ref_count"], 8)
        self.assertEqual(
            report["persist_credentials_false_count"], 8)
        self.assertEqual(report["uses_violations"], [])
        self.assertEqual(report["option_violations"], [])
        self.assertIn(
            "HEPTA_IBAPI_SDK_URL: "
            "${{ secrets.HEPTA_IBAPI_SDK_URL }}",
            workflow)
        self.assertNotIn(
            "HEPTA_IBAPI_SDK_URL: "
            "${{ vars.HEPTA_IBAPI_SDK_URL }}",
            workflow)
        ib_job = workflow.split(
            "  ibapi-linux-compile:\n", 1)[1].split(
                "\n  agent-os-source-contract:", 1)[0]
        ib_header = ib_job.split("\n    steps:", 1)[0]
        self.assertIn(
            "if: ${{ github.event_name != 'pull_request' && "
            "github.ref_name == github.event.repository.default_branch }}",
            ib_header)
        self.assertIn(
            "runs-on: [self-hosted, linux, x64, "
            "heptatrader-ib-sdk-v1]", ib_header)
        self.assertIn(
            "environment: heptatrader-ib-sdk-v1", ib_header)
        self.assertNotIn("HEPTA_IBAPI_SDK_URL", ib_header)
        self.assertEqual(
            ib_job.count(
                "HEPTA_IBAPI_SDK_URL: "
                "${{ secrets.HEPTA_IBAPI_SDK_URL }}"),
            2)

        weakened = workflow.replace(
            contract["ref"], "${{ github.sha }}", 1)
        drifted = quality._ci_checkout_contract(weakened, contract)
        self.assertFalse(drifted["passed"])
        self.assertEqual(drifted["pull_request_head_ref_count"], 7)

        credentials_enabled = workflow.replace(
            "          persist-credentials: false",
            "          persist-credentials: true",
            1)
        credentials_report = quality._ci_checkout_contract(
            credentials_enabled, contract)
        self.assertFalse(credentials_report["passed"])
        self.assertEqual(
            credentials_report["persist_credentials_false_count"], 7)
        self.assertTrue(credentials_report["option_violations"])

        extra_checkout_input = workflow.replace(
            "          fetch-depth: 2",
            "          fetch-depth: 2\n"
            "          submodules: recursive",
            1)
        extra_input_report = quality._ci_checkout_contract(
            extra_checkout_input, contract)
        self.assertFalse(extra_input_report["passed"])
        self.assertTrue(extra_input_report["option_violations"])

        quoted_checkout = workflow.replace(
            f"uses: {contract['action']}",
            f'"uses": {contract["action"]}',
            1)
        quoted_report = quality._ci_checkout_contract(
            quoted_checkout, contract)
        self.assertFalse(quoted_report["passed"])
        self.assertEqual(quoted_report["checkout_count"], 7)
        self.assertTrue(quoted_report["uses_violations"])

    def test_linux_ctest_lanes_use_restrictive_umask(self) -> None:
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        job_successors = {
            "agent-os-linux": "agent-os-no-git-ib-off",
            "agent-os-no-git-ib-off": "ctp-windows-source-boundary",
            "ibapi-linux-compile": "agent-os-source-contract",
            "nightly-native-sanitizers": "nightly-native-coverage",
            "nightly-native-coverage": None,
        }
        required_steps = {
            "agent-os-linux": {
                "Configure Agent OS tests": ("cmake -S .",),
                "Build Agent OS test binaries": ("cmake --build",),
                "Validate exact repository CTest inventory":
                    ("check_heptatrader_ctest_inventory.py",),
                "Run Agent OS CTest suite (includes IB-off install contract)":
                    ("ctest --test-dir",),
            },
            "agent-os-no-git-ib-off": {
                "Build and verify strict and Agent-OS-only source bundles":
                    ("build_heptatrader_clean_source_bundle.py",),
                "Configure no-Git Agent OS source": ("cmake \\",),
                "Build and smoke-test no-Git Agent OS source":
                    (
                        "cmake --build",
                        "check_heptatrader_ctest_inventory.py",
                        "check_hepta_execution_install_tree.py",
                    ),
            },
            "ibapi-linux-compile": {
                "Configure real IB-on Release target": ("cmake -S .",),
                "Compile real IB closure and offline test graph":
                    ("cmake --build",),
                "Validate exact real-IB repository CTest inventory":
                    ("check_heptatrader_ctest_inventory.py",),
                "Run targeted real-IB runtime and security tests":
                    ("ctest --test-dir", "--tests-regex",),
                "Validate full IB-on execution install component":
                    ("check_hepta_execution_install_tree.py",),
                "Build and verify no-Git Agent-OS source for IB-on":
                    ("build_heptatrader_clean_source_bundle.py",),
                "Configure no-Git real IB-on Release target": ("cmake \\",),
                "Build and smoke-test no-Git real IB-on source":
                    (
                        "cmake --build",
                        "check_heptatrader_ctest_inventory.py",
                        "check_hepta_execution_install_tree.py",
                    ),
            },
            "nightly-native-sanitizers": {
                "Build strict no-Git sanitizer source":
                    ("build_heptatrader_clean_source_bundle.py",),
                "Configure ${{ matrix.sanitizer }}": ("cmake -S",),
                "Validate private CTest inventory path":
                    ("--show-only=json-v1",),
                "Build ${{ matrix.sanitizer }}": ("cmake --build",),
                "Run and seal ${{ matrix.sanitizer }} suite":
                    ("run_heptatrader_ctest_evidence.py",),
            },
            "nightly-native-coverage": {
                "Build strict no-Git coverage source":
                    ("build_heptatrader_clean_source_bundle.py",),
                "Configure coverage build": ("cmake -S",),
                "Validate private CTest inventory path":
                    ("--show-only=json-v1",),
                "Build coverage suite": ("cmake --build",),
                "Run and seal coverage evidence":
                    ("run_heptatrader_coverage_evidence.py",),
            },
        }
        restrictive_prefix = (
            "        shell: bash\n"
            "        run: |\n"
            "          set -euo pipefail\n"
            "          umask 077\n")

        for job_name, steps in required_steps.items():
            job_marker = f"  {job_name}:\n"
            self.assertIn(job_marker, workflow)
            job = workflow.split(job_marker, 1)[1]
            successor = job_successors[job_name]
            if successor is not None:
                job = job.split(f"\n  {successor}:\n", 1)[0]
            for step_name, command_markers in steps.items():
                with self.subTest(job=job_name, step=step_name):
                    step_marker = f"      - name: {step_name}\n"
                    self.assertIn(step_marker, job)
                    step = job.split(step_marker, 1)[1].split(
                        "\n      - name:", 1)[0]
                    self.assertTrue(
                        step.startswith(restrictive_prefix), step)
                    umask_lines = [
                        line.strip()
                        for line in step.splitlines()
                        if line.strip().startswith("umask ")
                    ]
                    self.assertEqual(umask_lines, ["umask 077"])
                    for command_marker in command_markers:
                        self.assertIn(command_marker, step)
                        self.assertLess(
                            step.index("umask 077"),
                            step.index(command_marker))

    def test_canonical_ci_excludes_legacy_host_bound_gate(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        forbidden = policy["ci_archive"]["canonical_forbidden_fragments"]
        report = quality._ci_product_boundary(workflow, forbidden)
        self.assertTrue(report["passed"], report)
        drifted = quality._ci_product_boundary(
            workflow + "\n./scripts/ci_gate_pr.ps1\n", forbidden)
        self.assertFalse(drifted["passed"])
        self.assertEqual(
            drifted["forbidden_fragments"], ["scripts/ci_gate"])
        windows_spoof = quality._ci_product_boundary(
            workflow + "\npwsh -File Scripts\\CI_GATE_PR.ps1\n",
            forbidden)
        self.assertFalse(windows_spoof["passed"])
        self.assertEqual(
            windows_spoof["forbidden_fragments"], ["scripts/ci_gate"])

    def test_legacy_workflow_is_manual_non_executing_quarantine(self) -> None:
        relative = (
            ".github/workflows/legacy-research-windows-contract.yml")
        path = REPOSITORY / relative
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        if path.is_file():
            workflow = path.read_text(encoding="utf-8")
            expected_sha256 = policy["ci_archive"][
                "supplemental_workflow_sha256"][relative]
        else:
            self._strict_source_excludes(relative)
            workflow = MANUAL_QUARANTINE_FIXTURE
            expected_sha256 = hashlib.sha256(
                workflow.encode("utf-8")).hexdigest()
        report = quality._ci_manual_quarantine(
            workflow, expected_sha256)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["events"], ["workflow_dispatch"])
        self.assertNotIn(
            "$expected = '${{ inputs.reviewed_sha }}'",
            workflow)
        self.assertIn("persist-credentials: false", workflow)
        drifted = quality._ci_manual_quarantine(
            workflow.replace(
                "  workflow_dispatch:",
                "  push:\n  workflow_dispatch:",
                1) + "\n./scripts/ci_gate_release.ps1\n",
            expected_sha256)
        self.assertFalse(drifted["passed"])
        self.assertEqual(
            drifted["events"], ["push", "workflow_dispatch"])
        self.assertEqual(
            drifted["legacy_execution_fragments"],
            ["./scripts/ci_gate"])

        quoted_spoof = quality._ci_manual_quarantine(
            workflow.replace(
                "  workflow_dispatch:",
                "  \"push\":\n  workflow_dispatch:",
                1),
            expected_sha256)
        self.assertFalse(quoted_spoof["passed"])
        quoted_text = workflow.replace(
            "  workflow_dispatch:",
            "  \"push\":\n  workflow_dispatch:",
            1)
        quoted_self_bound = quality._ci_manual_quarantine(
            quoted_text,
            hashlib.sha256(quoted_text.encode("utf-8")).hexdigest())
        self.assertFalse(quoted_self_bound["passed"])
        self.assertEqual(
            quoted_self_bound["events"], ["push", "workflow_dispatch"])
        self.assertTrue(
            quoted_self_bound["event_syntax_violations"])
        windows_text = (
            workflow +
            "\npwsh -File Scripts\\CI_GATE_RELEASE.ps1\n")
        windows_self_bound = quality._ci_manual_quarantine(
            windows_text,
            hashlib.sha256(windows_text.encode("utf-8")).hexdigest())
        self.assertFalse(windows_self_bound["passed"])
        self.assertEqual(
            windows_self_bound["legacy_execution_fragments"],
            ["-File scripts/ci_gate"])
        comment_spoof = quality._ci_manual_quarantine(
            workflow.replace(
                "            'release_eligible=false',",
                "            '# release_eligible=false',",
                1),
            expected_sha256)
        self.assertFalse(comment_spoof["passed"])
        interpolation_spoof = quality._ci_manual_quarantine(
            workflow.replace(
                "$expected = $env:HEPTA_REVIEWED_SHA",
                "$expected = '${{ inputs.reviewed_sha }}'",
                1),
            expected_sha256)
        self.assertFalse(interpolation_spoof["passed"])
        self.assertTrue(
            interpolation_spoof["run_input_interpolations"])

    def test_ci_external_actions_are_pinned_to_full_commit_shas(self) -> None:
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        report = quality._ci_action_pinning(workflow)
        self.assertTrue(report["passed"], report)
        self.assertGreater(report["external_action_count"], 0)
        self.assertEqual(report["local_action_count"], 0)
        self.assertEqual(report["unbound_local_uses"], [])
        self.assertEqual(report["unpinned_actions"], [])
        self.assertEqual(report["uses_violations"], [])

        weakened = workflow.replace(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/checkout@v4",
            1)
        drifted = quality._ci_action_pinning(weakened)
        self.assertFalse(drifted["passed"])
        self.assertEqual(
            drifted["unpinned_actions"], ["actions/checkout@v4"])

    def test_controlled_coverage_toolchain_preflights_before_build(self) -> None:
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        coverage_job = workflow.split(
            "  nightly-native-coverage:\n", 1)[1]
        self.assertIn(
            "runs-on: [self-hosted, linux, x64, "
            "heptatrader-coverage-v1]", coverage_job)
        self.assertNotIn("pip install", coverage_job)
        self.assertNotIn("actions/setup-python@", coverage_job)
        preflight = coverage_job.index(
            "- name: Preflight controlled coverage toolchain")
        build_source = coverage_job.index(
            "- name: Build strict no-Git coverage source")
        configure = coverage_job.index(
            "- name: Configure coverage build")
        self.assertLess(preflight, build_source)
        self.assertLess(build_source, configure)
        expected_actionlint = (
            "self-hosted-runner:\n"
            "  labels:\n"
            "    - heptatrader-coverage-v1\n"
            "    - heptatrader-ib-sdk-v1\n"
            "\n"
            "config-variables: null\n"
            "\n"
            "paths:\n")
        actionlint_relative = ".github/actionlint.yaml"
        actionlint_path = REPOSITORY / actionlint_relative
        if actionlint_path.is_file():
            actionlint = actionlint_path.read_text(encoding="utf-8")
        else:
            self._strict_source_excludes(actionlint_relative)
            actionlint = expected_actionlint
        self.assertEqual(actionlint, expected_actionlint)

    def test_ci_action_parser_rejects_noncanonical_uses_forms(self) -> None:
        digest = "0123456789abcdef0123456789abcdef01234567"
        canonical = (
            "jobs:\n"
            "  audit:\n"
            "    steps:\n"
            "      - name: Run reviewed action\n"
            f"        uses: owner/action@{digest}\n")
        report = quality._ci_action_pinning(canonical)
        self.assertTrue(report["passed"], report)

        variants = {
            "quoted-key": canonical.replace(
                "        uses:", '        "uses":', 1),
            "space-before-colon": canonical.replace(
                "        uses:", "        uses :", 1),
            "block-scalar": canonical.replace(
                f"        uses: owner/action@{digest}",
                "        uses: >-\n"
                f"          owner/action@{digest}",
                1),
            "explicit-multiline-key": canonical.replace(
                f"        uses: owner/action@{digest}",
                '        ? "u\\\n'
                '          ses"\n'
                f"        : owner/action@{digest}",
                1),
            "tagged-key": canonical.replace(
                "        uses:", "        !!str uses:", 1),
            "flow-map-tagged-key": (
                "jobs:\n"
                "  audit:\n"
                f"    steps: [{{name: x, !!str uses: "
                f"owner/action@{digest}}}]\n"),
        }
        for label, workflow in variants.items():
            with self.subTest(label=label):
                drifted = quality._ci_action_pinning(workflow)
                self.assertFalse(drifted["passed"], drifted)
                self.assertEqual(drifted["external_action_count"], 0)
                self.assertTrue(drifted["uses_violations"])

        local = canonical.replace(
            f"owner/action@{digest}", "./.github/actions/reviewed", 1)
        local_report = quality._ci_action_pinning(local)
        self.assertFalse(local_report["passed"], local_report)
        self.assertEqual(
            local_report["unbound_local_uses"],
            ["./.github/actions/reviewed"])

    def test_ci_source_extraction_preserves_manifested_modes(self) -> None:
        workflow = (
            REPOSITORY / ".github/workflows/ci-gate.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("tar -xf", workflow)
        self.assertEqual(
            workflow.count("tar --same-permissions -xf"), 4)

    def test_archive_manifest_references_bind_product_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "policy.json"
            manifest.write_text(
                json.dumps({
                    "include_files": ["cmake/gate.cmake"],
                    "required_files": ["scripts/check.py"],
                }) + "\n",
                encoding="utf-8")
            manifest.chmod(0o600)
            self.assertEqual(
                quality._archive_manifest_references(root, "policy.json"),
                {
                    "policy.json",
                    "cmake/gate.cmake",
                    "scripts/check.py",
                })
            manifest.write_text(
                json.dumps({
                    "include_files": ["../escape"],
                    "required_files": ["scripts/check.py"],
                }) + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(
                    quality.CodeQualityError, "unsafe archive member"):
                quality._archive_manifest_references(root, "policy.json")

    def test_ci_archive_inputs_require_regular_bound_git_blobs(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-ci-archive-") as directory:
            root = Path(directory)
            regular = root / "scripts/check.py"
            regular.parent.mkdir()
            regular.write_text("print('bound')\n", encoding="utf-8")
            regular.chmod(0o644)
            link = root / "scripts/check-link.py"
            link.symlink_to("check.py")
            commands = (
                ["git", "init", "-q"],
                ["git", "config", "user.name", "Hepta Test"],
                ["git", "config", "user.email", "hepta@example.invalid"],
                ["git", "add", "scripts/check.py", "scripts/check-link.py"],
                ["git", "commit", "-q", "-m", "fixture"],
            )
            for command in commands:
                completed = subprocess.run(
                    command, cwd=root, check=False,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True)
                self.assertEqual(
                    completed.returncode, 0,
                    completed.stdout + completed.stderr)
            entries = quality._git_archive_entries(root, "HEAD")
            self.assertEqual(
                entries["scripts/check.py"]["kind"], "regular")
            self.assertEqual(
                entries["scripts/check.py"]["mode"], "0644")
            self.assertEqual(
                entries["scripts/check-link.py"]["kind"], "symlink")
            self.assertIsNone(quality._archive_input_problem(
                root, entries, "scripts/check.py"))
            self.assertIn(
                "member type is symlink",
                quality._archive_input_problem(
                    root, entries, "scripts/check-link.py") or "")
            regular.write_text("print('drift')\n", encoding="utf-8")
            self.assertIn(
                "bytes differ",
                quality._archive_input_problem(
                    root, entries, "scripts/check.py") or "")

    def test_gateway_symbol_gate_separates_release_budget_from_denylist(
            self) -> None:
        generic_symbols = [
            f"{index:08x} T fixture_symbol_{index}"
            for index in range(1201)
        ]
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            release = self._run_gateway_symbol_gate(
                directory, generic_symbols, enforce_budget="ON")
            self.assertNotEqual(release.returncode, 0)
            self.assertIn(
                "defined-symbol budget exceeded",
                release.stdout + release.stderr)

            instrumented = self._run_gateway_symbol_gate(
                directory, generic_symbols, enforce_budget="OFF")
            self.assertEqual(
                instrumented.returncode,
                0,
                instrumented.stdout + instrumented.stderr)
            self.assertIn(
                "Release-only quantitative budget not enforced",
                instrumented.stdout + instrumented.stderr)

            privileged = self._run_gateway_symbol_gate(
                directory,
                ["00000000 T ExecutionCoordinator::Dispatch()"],
                enforce_budget="OFF")
            self.assertNotEqual(privileged.returncode, 0)
            self.assertIn(
                "contains privileged Execution Service symbols",
                privileged.stdout + privileged.stderr)

            implicit = self._run_gateway_symbol_gate(
                directory, ["00000000 T fixture"], enforce_budget=None)
            self.assertNotEqual(implicit.returncode, 0)
            self.assertIn(
                "must be explicitly ON or OFF",
                implicit.stdout + implicit.stderr)

    def test_gateway_release_dead_code_elimination_contract_is_fail_closed(
            self) -> None:
        cmake_text = (
            REPOSITORY / "HeptaTrade/CMakeLists.txt"
        ).read_text(encoding="utf-8")
        closure = """set(HEPTA_GATEWAY_RELEASE_SECTION_TARGETS
        hepta_execution_contract
        hepta_execution_transport
        hepta_execution_client
        hepta_agent_execution_support
        hepta_trading_tool_core
        hepta_agent_os_core)"""
        function_sections = "$<$<CONFIG:Release>:-ffunction-sections>"
        data_sections = "$<$<CONFIG:Release>:-fdata-sections>"
        no_rtti = "$<$<CONFIG:Release>:-fno-rtti>"
        link_gc = "PROPERTY LINK_FLAGS_RELEASE \" -Wl,--gc-sections\""
        required_fragments = (
            closure, function_sections, data_sections, no_rtti, link_gc)

        def contract_present(document: str) -> bool:
            return (
                closure in document
                and document.count(function_sections) == 2
                and document.count(data_sections) == 2
                and document.count(no_rtti) == 2
                and link_gc in document
            )

        self.assertTrue(contract_present(cmake_text))
        for fragment in required_fragments:
            with self.subTest(removed=fragment):
                mutated = cmake_text.replace(fragment, "", 1)
                self.assertFalse(contract_present(mutated))

    def test_policy_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(
                quality.CodeQualityError, "unavailable"):
            quality.audit(
                REPOSITORY,
                REPOSITORY / "policies/does-not-exist.json")
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        policy["coverage"]["toolchain"]["provisioned"] = True
        with self.assertRaisesRegex(
                quality.CodeQualityError, "identity is invalid"):
            self._audit_with_document(policy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
