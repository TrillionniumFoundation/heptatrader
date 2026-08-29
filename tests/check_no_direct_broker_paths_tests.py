#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_no_direct_broker_paths", ROOT / "scripts" / "check_no_direct_broker_paths.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

RETIRED_ENTRYPOINT_FIXTURES = {
    "fx_strategy_paper.py": b"""#!/usr/bin/env python3
\"\"\"Fail-closed compatibility entrypoint for the retired direct-IB strategy.\"\"\"

import sys


def main() -> int:
    print(
        "ERROR: direct broker strategies are quarantined. "
        "Use heptactl/MCP through hepta-tool-gatewayd and the Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "ib_paper_order_loop.py": b"""#!/usr/bin/env python3
\"\"\"Fail-closed compatibility entrypoint for the retired direct-IB loop.\"\"\"

import sys


def main() -> int:
    print(
        "ERROR: direct broker order loops are quarantined. "
        "Use heptactl/MCP through hepta-tool-gatewayd and the Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "xt_first_live_order.py": b"""#!/usr/bin/env python3
\"\"\"Fail-closed compatibility entrypoint for retired direct XT/QMT trading.\"\"\"

import sys


def main() -> int:
    print(
        "ERROR: direct XT/QMT broker scripts are quarantined. "
        "Use MCP/heptactl through hepta-tool-gatewayd and the Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "xt_first_live_order_sim.py": b"""#!/usr/bin/env python3
\"\"\"Fail-closed compatibility entrypoint for retired direct XT/QMT simulation.\"\"\"

import sys


def main() -> int:
    print(
        "ERROR: direct XT/QMT broker scripts are quarantined. "
        "Use the deterministic venue through the canonical Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())

""",
}


def retired_entrypoint_fixture(entrypoint: str) -> bytes:
    data = RETIRED_ENTRYPOINT_FIXTURES[entrypoint]
    relative = f"scripts/{entrypoint}"
    expected = MODULE.EXPECTED_RETIRED_COMPATIBILITY_SHA256[relative]
    observed = hashlib.sha256(data).hexdigest()
    if observed != expected:
        raise AssertionError(
            f"test fixture digest drifted for {relative}: {observed}")
    return data


class DirectBrokerGateTests(unittest.TestCase):
    @staticmethod
    def _record(root: pathlib.Path, relative: str) -> dict:
        path = root / relative
        path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        data = path.read_bytes()
        return {
            "mode": "0755" if path.stat().st_mode & 0o111 else "0644",
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    def write_profile_marker(
            self, root: pathlib.Path, *, agent_source: bool,
            included_paths: set[str]) -> pathlib.Path:
        gate_relative = "scripts/check_no_direct_broker_paths.py"
        if gate_relative in included_paths and not (root / gate_relative).exists():
            (root / gate_relative).write_bytes(
                (ROOT / gate_relative).read_bytes())
            (root / gate_relative).chmod(0o644)
        records = [
            self._record(root, relative)
            for relative in sorted(included_paths)
        ]
        files_sha256 = hashlib.sha256(json.dumps(
            records, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode("utf-8")).hexdigest()
        common = {
            "root": root.name,
            "file_count": len(records),
            "files_sha256": files_sha256,
            "paper_authorized": False,
            "live_authorized": False,
            "files": records,
        }
        if agent_source:
            document = {
                **common,
                "schema": "hepta.agent-os-source-bundle.v1",
                "version": 1,
                "bundle_class": "agent-os-source-only",
                "release_version": "fixture",
                "policy_sha256": "1" * 64,
                "parent_strict_source": {
                    "schema": "hepta.clean-source-bundle.v2",
                    "git_head": "2" * 40,
                    "root": "heptatrader-fixture",
                    "file_count": len(records),
                    "files_sha256": "3" * 64,
                    "bundle_sha256": "4" * 64,
                    "manifest_sha256": "5" * 64,
                },
                "excluded_non_product_prefixes": [],
                "excluded_non_product_files": [],
                "excluded_legacy_prefixes": [],
                "excluded_legacy_files": [],
            }
            name = "agent-os-source-manifest.json"
        else:
            document = {
                **common,
                "schema": "hepta.clean-source-bundle.v2",
                "bundle_class": "strict-source-only",
                "version": "agent-os-fixture",
                "git_head": "6" * 40,
                "security_manifest_sha256": "sha256:" + "7" * 64,
                "security_manifest_file_count": 1,
                "excluded_unsafe_tree": "compat/unsafe-direct-broker",
                "excluded_legacy_runtime_tree": "Tools",
                "excluded_nonredistributable_vendor_prefixes": [],
                "redistributable_vendor_metadata_allowlist": [],
                "nonredistributable_vendor_payload_included": False,
                "excluded_prebuilt_payload_paths": [],
                "excluded_prebuilt_overlay_prefixes": [],
                "compiled_payload_suffixes_denied": [],
                "compiled_payload_policy_version":
                    "hepta.strict-source-payload-policy.v1",
                "compiled_payload_policy_sha256": "sha256:" + "8" * 64,
                "prebuilt_payload_included": False,
            }
            name = "source-bundle-manifest.json"
        marker = root / ".hepta" / name
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        marker.chmod(0o644)
        return marker

    def fixture(self) -> pathlib.Path:
        temporary_base = pathlib.Path(
            self.enterContext(tempfile.TemporaryDirectory()))
        temporary = temporary_base / "heptatrader-agent-os-fixture"
        (temporary / "HeptaTrade/adapter_ib").mkdir(parents=True)
        (temporary / "scripts").mkdir()
        (temporary / "policies").mkdir()
        (temporary / "HeptaTrade/adapter_ib/ib_api_wrapper.cpp").write_text(
            "bool PlaceOrder() { return false; }\n"
            "bool PlaceOrder() { return false; }\n"
            "bool CancelOrder() { return false; }\n"
            "bool CancelOrder() { return false; }\n"
            "client.placeOrder(1, contract, order);\n"
            "client.cancelOrder(1);\n",
            encoding="utf-8",
        )
        legacy_sources = {
            "HeptaTrade/HeptaDemoStrategyTrader.cpp": (
                "m_ibAdapter.PlaceOrder(contract, order, &order_id);\n"
                "m_ibAdapter.PlaceOrderCorrelated(contract, order, id, &order_id);\n"
                "m_ibAdapter.CancelOrder(order_id);\n"
                "m_TradeChannel.CancelOrder(order);\n"
            ),
            "HeptaTrade/order_watchdog.cpp": (
                "ctpAdapter->CancelOrder(order_id);\n"
                "ctpAdapter->CancelOrder(order_id);\n"
                "ibAdapter->CancelOrder(order_id);\n"
            ),
        }
        frozen_legacy_adapter_sources = {
            "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp": (
                "bool HeptaXTGatewayAdapter::PlaceOrder() { return false; }\n"
                "bool HeptaXTGatewayAdapter::CancelOrder() { return false; }\n"
            ),
            "HeptaTrade/adapter_xt/xt_gateway_adapter.h": (
                "bool PlaceOrder();\n"
                "bool CancelOrder();\n"
            ),
        }
        for relative, source in legacy_sources.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        for relative, source in frozen_legacy_adapter_sources.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        (temporary / MODULE.AGENT_OS_SOURCE_POLICY).write_text(
            json.dumps({
                "schema": "hepta.agent-os-source-policy.v2",
                "forbidden_files": sorted(legacy_sources),
                "forbidden_prefixes": ["HeptaTrade/adapter_xt/"],
            }) + "\n",
            encoding="utf-8",
        )
        for retired in MODULE.RETIRED_ENTRYPOINTS:
            (temporary / "scripts" / retired).write_bytes(
                retired_entrypoint_fixture(retired))
        for runner in MODULE.CANONICAL_RUNNERS:
            (temporary / runner).write_text(
                "offline_tool_gateway_regression = True\n", encoding="utf-8"
            )
        return temporary

    def test_allows_only_the_broker_adapter_binding(self):
        self.assertEqual(MODULE.violations(self.fixture()), [])

    def test_strict_bundle_manifest_controls_compatibility_shim_presence(
            self):
        root = self.fixture()
        gate = root / "scripts/check_no_direct_broker_paths.py"
        gate.write_text("# strict bundle gate\n", encoding="utf-8")
        included_shims = {
            "scripts/ib_paper_order_loop.py",
            "scripts/fx_strategy_paper.py",
        }
        gate.chmod(0o644)
        self.write_profile_marker(
            root, agent_source=False,
            included_paths={
                gate.relative_to(root).as_posix(), *included_shims})
        for name in (
                "xt_first_live_order.py",
                "xt_first_live_order_sim.py"):
            (root / "scripts" / name).unlink()
        self.assertTrue(MODULE._strict_source_only(root))
        self.assertEqual(MODULE.violations(root), [])
        excluded = root / "scripts/xt_first_live_order.py"
        excluded.write_bytes(
            retired_entrypoint_fixture("xt_first_live_order.py"))
        self.assertTrue(any(
            "excluded compatibility shim is materialized" in failure
            for failure in MODULE.violations(root)))

    def test_rejects_direct_script_mutation(self):
        root = self.fixture()
        (root / "scripts/unsafe.py").write_text(
            "self.placeOrder(order_id, c, o)\n", encoding="utf-8"
        )
        self.assertTrue(any(
            "scripts/unsafe.py:1" in item
            for item in MODULE.violations(root)
        ))

    def test_read_only_agent_module_cannot_import_network_or_broker_sdk(self):
        root = self.fixture()
        source = root / "scripts/hepta_market_unsafe.py"
        source.write_text(
            "import socket\n"
            "from urllib.request import urlopen\n"
            "BROKER_PORT = 4002\n",
            encoding="utf-8",
        )
        failures = MODULE.violations(root)
        self.assertTrue(any(
            "read-only Agent module imports a broker/network client" in item
            for item in failures
        ))
        self.assertTrue(any(
            "read-only Agent module embeds a broker endpoint" in item
            for item in failures
        ))

    def test_bounded_shadow_module_cannot_import_remote_client(self):
        root = self.fixture()
        source = root / "scripts/hepta_bounded_shadow_unsafe.py"
        source.write_text("import socket\n", encoding="utf-8")
        failures = MODULE.violations(root)
        self.assertTrue(any(
            "read-only Agent module imports a broker/network client" in item
            for item in failures
        ))

    def test_rejects_duplicate_reviewed_vendor_callsite(self):
        root = self.fixture()
        wrapper = root / "HeptaTrade/adapter_ib/ib_api_wrapper.cpp"
        wrapper.write_text(
            wrapper.read_text(encoding="utf-8") +
            "client.placeOrder(2, contract, order);\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "reviewed IB broker mutation placeOrder count drifted" in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_duplicate_reviewed_adapter_callsite(self):
        root = self.fixture()
        source = (
            ROOT / "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp"
        ).read_text(encoding="utf-8")
        adapter = (
            root /
            "HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp"
        )
        adapter.write_text(
            source + "\nm_api->PlaceOrder(orderId, contract, order);\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "reviewed direct adapter mutation call m_api.PlaceOrder "
            "count drifted" in item
            for item in MODULE.violations(root)
        ))

    def test_reduce_only_adapter_callsite_is_exactly_single_authority_path(self):
        root = self.fixture()
        relative = (
            "HeptaTrade/execution/"
            "ib_paper_execution_runtime_policy.cpp"
        )
        source = (ROOT / relative).read_text(encoding="utf-8")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            source +
            "\nm_adapter->PlaceReduceOnlyOrderCorrelated();\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "reviewed direct adapter mutation call "
            "m_adapter.PlaceReduceOnlyOrderCorrelated count drifted"
            in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_reduce_only_adapter_call_outside_authority_path(self):
        root = self.fixture()
        relative = "HeptaTrade/unreviewed_reduce_only_path.cpp"
        (root / relative).write_text(
            "m_adapter->PlaceReduceOnlyOrderCorrelated();\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            relative in item and
            "unreviewed direct adapter mutation call "
            "m_adapter.PlaceReduceOnlyOrderCorrelated" in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_multiline_vendor_mutation(self):
        root = self.fixture()
        (root / "scripts/unsafe_multiline.py").write_text(
            "trader.order_stock\n"
            "(account, symbol, side, qty, price_type, price)\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "scripts/unsafe_multiline.py:1: direct XT_QMT" in item
            for item in MODULE.violations(root)
        ))

    def test_scans_policy_selected_adapter_and_plugin_roots(self):
        root = self.fixture()
        adapter = root / "adapters/mcp/direct_broker.py"
        adapter.parent.mkdir(parents=True)
        adapter.write_text(
            "client.placeOrder\n(1, contract, order)\n",
            encoding="utf-8",
        )
        plugin = root / "plugins/hepta/direct_broker.py"
        plugin.parent.mkdir(parents=True)
        plugin.write_text(
            "trader.cancel_order_stock\n(account, order_id)\n",
            encoding="utf-8",
        )
        failures = MODULE.violations(root)
        self.assertTrue(any(
            "adapters/mcp/direct_broker.py:1: direct IB" in item
            for item in failures
        ))
        self.assertTrue(any(
            "plugins/hepta/direct_broker.py:1: direct XT_QMT" in item
            for item in failures
        ))

    def test_scans_new_source_policy_prefix(self):
        root = self.fixture()
        policy = root / MODULE.AGENT_OS_SOURCE_POLICY
        document = json.loads(policy.read_text(encoding="utf-8"))
        document["include_prefixes"] = ["runtime_product/"]
        document["include_files"] = []
        policy.write_text(
            json.dumps(document, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        source = root / "runtime_product/tool.py"
        source.parent.mkdir()
        source.write_text(
            "api.ReqOrderInsert\n(request, request_id)\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "runtime_product/tool.py:1: direct CTP" in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_ib_vendor_mutation_symbols(self):
        cases = (
            "client.placeOrder(1, contract, order)",
            "client.cancelOrder(1)",
            "client.exerciseOptions(1, contract, 1, 1, account, 0)",
            "client.reqGlobalCancel()",
        )
        for index, source in enumerate(cases):
            with self.subTest(source=source):
                root = self.fixture()
                relative = f"scripts/unsafe_ib_{index}.py"
                (root / relative).write_text(source + "\n", encoding="utf-8")
                self.assertTrue(any(
                    relative in item and "direct IB broker mutation" in item
                    for item in MODULE.violations(root)
                ))

    def test_rejects_ctp_vendor_mutation_symbols(self):
        cases = (
            "api.ReqOrderInsert(request, request_id)",
            "api.ReqOrderAction(request, request_id)",
            "api.ReqExecOrderInsert(request, request_id)",
            "api.ReqQuoteAction(request, request_id)",
            "api.ReqCombActionInsert(request, request_id)",
        )
        for index, source in enumerate(cases):
            with self.subTest(source=source):
                root = self.fixture()
                relative = f"scripts/unsafe_ctp_{index}.py"
                (root / relative).write_text(source + "\n", encoding="utf-8")
                self.assertTrue(any(
                    relative in item and "direct CTP broker mutation" in item
                    for item in MODULE.violations(root)
                ))

    def test_rejects_xt_qmt_vendor_mutation_symbols(self):
        cases = (
            "trader.order_stock(account, symbol, side, qty, price_type, price)",
            "trader.order_stock_async(account, symbol, side, qty, price_type, price)",
            "trader.cancel_order_stock(account, order_id)",
            "trader.cancel_order_stock_async(account, order_id)",
        )
        for index, source in enumerate(cases):
            with self.subTest(source=source):
                root = self.fixture()
                relative = f"scripts/unsafe_xt_{index}.py"
                (root / relative).write_text(source + "\n", encoding="utf-8")
                self.assertTrue(any(
                    relative in item and "direct XT_QMT broker mutation" in item
                    for item in MODULE.violations(root)
                ))

    def test_rejects_generic_adapter_mutation_symbols(self):
        cases = (
            "adapter.submit_order(intent)",
            "adapter.insert_order(intent)",
            "adapter.send_order(intent)",
            "adapter.place_order(intent)",
            "adapter.cancel_order(order_id)",
            "adapter.withdraw_order(order_id)",
            "adapter.submitOrder(intent)",
        )
        for index, source in enumerate(cases):
            with self.subTest(source=source):
                root = self.fixture()
                relative = f"scripts/unsafe_adapter_{index}.py"
                (root / relative).write_text(source + "\n", encoding="utf-8")
                self.assertTrue(any(
                    relative in item and
                    "direct GENERIC_ADAPTER broker mutation" in item
                    for item in MODULE.violations(root)
                ))

    def test_rejects_new_adapter_mutation_surface(self):
        root = self.fixture()
        adapter = root / "HeptaTrade/adapter_new/new_gateway_adapter.cpp"
        adapter.parent.mkdir(parents=True)
        adapter.write_text(
            "bool NewGatewayAdapter::SubmitOrder() { return false; }\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "unreviewed adapter mutation symbol SubmitOrder" in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_unreviewed_direct_adapter_call(self):
        root = self.fixture()
        source = root / "HeptaTrade/new_execution_path.cpp"
        source.write_text(
            "bool Send(NewBrokerAdapter* adapter) {\n"
            "    return adapter->SubmitOrder();\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "unreviewed direct adapter mutation call adapter.SubmitOrder" in item
            for item in MODULE.violations(root)
        ))

    def test_frozen_legacy_exception_must_be_forbidden_by_source_policy(self):
        root = self.fixture()
        policy = root / MODULE.AGENT_OS_SOURCE_POLICY
        policy.write_text(json.dumps({
            "schema": "hepta.agent-os-source-policy.v2",
            "forbidden_files": ["HeptaTrade/HeptaDemoStrategyTrader.cpp"],
            "forbidden_prefixes": ["HeptaTrade/adapter_xt/"],
        }) + "\n", encoding="utf-8")
        self.assertTrue(any(
            "HeptaTrade/order_watchdog.cpp: frozen broker mutation exception "
            "is not forbidden" in item
            for item in MODULE.violations(root)
        ))

    def test_frozen_xt_adapter_exception_must_be_forbidden_by_source_policy(self):
        root = self.fixture()
        policy = root / MODULE.AGENT_OS_SOURCE_POLICY
        policy.write_text(json.dumps({
            "schema": "hepta.agent-os-source-policy.v2",
            "forbidden_files": sorted(
                MODULE.FROZEN_LEGACY_ADAPTER_MUTATION_CALLS
            ),
            "forbidden_prefixes": [],
        }) + "\n", encoding="utf-8")
        self.assertTrue(any(
            "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp: frozen broker "
            "mutation exception is not forbidden" in item
            for item in MODULE.violations(root)
        ))

    def test_frozen_legacy_file_cannot_add_a_new_mutation_symbol(self):
        root = self.fixture()
        legacy = root / "HeptaTrade/HeptaDemoStrategyTrader.cpp"
        legacy.write_text(
            legacy.read_text(encoding="utf-8")
            + "m_ibAdapter.SubmitOrder(order);\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "unreviewed direct adapter mutation call "
            "m_ibAdapter.SubmitOrder" in item
            for item in MODULE.violations(root)
        ))

    def test_rejects_new_symbol_in_reviewed_adapter_file(self):
        root = self.fixture()
        adapter = root / "HeptaTrade/adapter_ib/ib_api_wrapper.cpp"
        adapter.write_text(
            "bool Wrapper::PlaceOrder() { return false; }\n"
            "bool Wrapper::SubmitOrder() { return false; }\n",
            encoding="utf-8",
        )
        failures = MODULE.violations(root)
        self.assertFalse(any(
            "unreviewed adapter mutation symbol PlaceOrder" in item
            for item in failures
        ))
        self.assertTrue(any(
            "unreviewed adapter mutation symbol SubmitOrder" in item
            for item in failures
        ))

    def test_rejects_runner_reference_to_retired_entrypoint(self):
        root = self.fixture()
        (root / "scripts/strategy_iterate_paper.py").write_text(
            "run('ib_paper_order_loop.py')\n", encoding="utf-8"
        )
        self.assertTrue(any("retired direct broker" in item for item in MODULE.violations(root)))

    def test_rejects_ibapi_import_in_compatibility_shim(self):
        root = self.fixture()
        (root / "scripts/fx_strategy_paper.py").write_text(
            "from ibapi.client import EClient\ndef main():\n    return 78\n", encoding="utf-8"
        )
        self.assertTrue(any(
            "imports a broker SDK" in item for item in MODULE.violations(root)
        ))

    def test_rejects_xtquant_import_in_compatibility_shim(self):
        root = self.fixture()
        (root / "scripts/xt_first_live_order.py").write_text(
            "from xtquant.xttrader import XtQuantTrader\n"
            "def main():\n"
            "    return 78\n",
            encoding="utf-8",
        )
        self.assertTrue(any(
            "imports a broker SDK" in item for item in MODULE.violations(root)
        ))

    def test_rejects_retired_shim_contract_substitutions(self):
        cases = {
            "comment-only-return-78": (
                "scripts/ib_paper_order_loop.py",
                "# return 78\n\ndef main():\n    return 0\n",
            ),
            "return-zero": (
                "scripts/fx_strategy_paper.py",
                "def main():\n    return 0\n",
            ),
            "dynamic-import": (
                "scripts/xt_first_live_order.py",
                "def main():\n"
                "    __import__('xt' + 'quant')\n"
                "    return 78\n",
            ),
            "dynamic-getattr": (
                "scripts/xt_first_live_order_sim.py",
                "def main(trader=None):\n"
                "    getattr(trader, 'order_' + 'stock')()\n"
                "    return 78\n",
            ),
        }
        for label, (relative, replacement) in cases.items():
            with self.subTest(case=label):
                root = self.fixture()
                (root / relative).write_text(
                    replacement, encoding="utf-8")
                failures = MODULE.violations(root)
                self.assertTrue(any(
                    relative in failure and
                    MODULE.RETIRED_COMPATIBILITY_CONTRACT_VERSION in failure
                    for failure in failures))

    def test_profile_marker_requires_exact_schema_and_closure_digest(self):
        root = self.fixture()
        included = {
            "scripts/check_no_direct_broker_paths.py",
            *{
                f"scripts/{entrypoint}"
                for entrypoint in MODULE.RETIRED_ENTRYPOINTS
            },
        }
        marker = self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        valid = json.loads(marker.read_text(encoding="utf-8"))
        cases = {}

        minimal = {
            "schema": "hepta.clean-source-bundle.v2",
            "root": root.name,
            "file_count": 1,
            "paper_authorized": False,
            "live_authorized": False,
            "files": [{"path": "scripts/check_no_direct_broker_paths.py"}],
        }
        cases["minimal-marker"] = minimal

        extra_field = json.loads(json.dumps(valid))
        extra_field["profile"] = "strict"
        cases["extra-field"] = extra_field

        unsorted = json.loads(json.dumps(valid))
        unsorted["files"][0], unsorted["files"][1] = (
            unsorted["files"][1], unsorted["files"][0])
        unsorted["files_sha256"] = hashlib.sha256(json.dumps(
            unsorted["files"], ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode("utf-8")).hexdigest()
        cases["unsorted-records"] = unsorted

        noncanonical = json.loads(json.dumps(valid))
        noncanonical["files"][0]["path"] = (
            noncanonical["files"][0]["path"].replace("/", "//", 1))
        noncanonical["files_sha256"] = hashlib.sha256(json.dumps(
            noncanonical["files"], ensure_ascii=True, separators=(",", ":"),
            sort_keys=True).encode("utf-8")).hexdigest()
        cases["noncanonical-path"] = noncanonical

        digest_drift = json.loads(json.dumps(valid))
        digest_drift["files_sha256"] = "f" * 64
        cases["files-digest-drift"] = digest_drift

        for label, document in cases.items():
            with self.subTest(case=label):
                marker.write_text(
                    json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
                marker.chmod(0o644)
                self.assertFalse(MODULE._strict_source_only(root))
                self.assertTrue(any(
                    "invalid source profile" in failure
                    for failure in MODULE.violations(root)))
        marker.write_text(
            json.dumps(valid, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        marker.chmod(0o644)
        self.assertTrue(MODULE._strict_source_only(root))

    def test_profile_marker_rejects_dual_markers_and_git_metadata(self):
        included = {
            "scripts/check_no_direct_broker_paths.py",
            *{
                f"scripts/{entrypoint}"
                for entrypoint in MODULE.RETIRED_ENTRYPOINTS
            },
        }

        root = self.fixture()
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        self.write_profile_marker(
            root, agent_source=True, included_paths=included)
        self.assertTrue(any(
            "both present" in failure
            for failure in MODULE.violations(root)))

        root = self.fixture()
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        (root / ".git").mkdir()
        self.assertTrue(any(
            "Git metadata is forbidden" in failure
            for failure in MODULE.violations(root)))

        root = self.fixture()
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        os.symlink("missing-git-dir", root / ".git")
        self.assertTrue(any(
            "Git metadata is forbidden" in failure
            for failure in MODULE.violations(root)))

    def test_profile_marker_requires_no_follow_and_single_link(self):
        included = {
            "scripts/check_no_direct_broker_paths.py",
            *{
                f"scripts/{entrypoint}"
                for entrypoint in MODULE.RETIRED_ENTRYPOINTS
            },
        }

        root = self.fixture()
        marker = self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        backing = marker.with_name("manifest.backing.json")
        marker.rename(backing)
        os.symlink(backing.name, marker)
        self.assertTrue(any(
            "cannot open anchored regular file" in failure
            for failure in MODULE.violations(root)))

        root = self.fixture()
        marker = self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        os.link(marker, marker.with_name("manifest-hardlink.json"))
        self.assertTrue(any(
            "single-link, non-symlink" in failure
            for failure in MODULE.violations(root)))

        root = self.fixture()
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        marker_directory = root / ".hepta"
        backing_directory = root / ".hepta-real"
        marker_directory.rename(backing_directory)
        os.symlink(backing_directory.name, marker_directory)
        self.assertTrue(any(
            "cannot open anchored directory" in failure
            for failure in MODULE.violations(root)))

    def test_profile_record_rejects_intermediate_symlink(self):
        root = self.fixture()
        payload_directory = root / "payload-real"
        payload_directory.mkdir()
        (payload_directory / "record.txt").write_text(
            "anchored payload\n", encoding="utf-8")
        os.symlink(payload_directory.name, root / "payload-link")
        included = {
            "payload-link/record.txt",
            "scripts/check_no_direct_broker_paths.py",
            *{
                f"scripts/{entrypoint}"
                for entrypoint in MODULE.RETIRED_ENTRYPOINTS
            },
        }
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        self.assertTrue(any(
            "profile-record payload is unsafe" in failure and
            "cannot open anchored directory" in failure
            for failure in MODULE.violations(root)))

    def test_profile_marker_rejects_metadata_drift_during_read(self):
        root = self.fixture()
        included = {
            "scripts/check_no_direct_broker_paths.py",
            *{
                f"scripts/{entrypoint}"
                for entrypoint in MODULE.RETIRED_ENTRYPOINTS
            },
        }
        self.write_profile_marker(
            root, agent_source=False, included_paths=included)
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(descriptor):
            nonlocal calls
            calls += 1
            observed = real_fstat(descriptor)
            if calls != 2:
                return observed
            return types.SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_nlink=observed.st_nlink,
                st_size=observed.st_size + 1,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
            )

        with mock.patch.object(
                MODULE.os, "fstat", side_effect=drifting_fstat):
            self.assertTrue(any(
                "anchored directory changed during read" in failure
                for failure in MODULE.violations(root)))

    def test_anchored_payload_binds_mode_to_same_descriptor_snapshot(self):
        root = self.fixture()
        payload = root / "payload.txt"
        payload.write_text("payload\n", encoding="utf-8")
        payload.chmod(0o644)
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(descriptor):
            nonlocal calls
            calls += 1
            observed = real_fstat(descriptor)
            if calls != 3:
                return observed
            return types.SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode | 0o111,
                st_nlink=observed.st_nlink,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
            )

        with mock.patch.object(
                MODULE.os, "fstat", side_effect=drifting_fstat):
            with self.assertRaisesRegex(
                    ValueError, "changed during stable read"):
                MODULE._stable_anchored_regular(
                    root, "payload.txt", limit=payload.stat().st_size)

    def test_repository_xt_entrypoints_are_fail_closed(self):
        entrypoints = (
            "xt_first_live_order.py",
            "xt_first_live_order_sim.py",
        )
        markers = (
            (
                ROOT / ".hepta/agent-os-source-manifest.json",
                "hepta.agent-os-source-bundle.v1",
                MODULE._agent_os_source_only,
            ),
            (
                ROOT / ".hepta/source-bundle-manifest.json",
                "hepta.clean-source-bundle.v2",
                MODULE._strict_source_only,
            ),
        )
        selected = next(
            (item for item in markers if item[0].is_file()), None)
        if selected is not None:
            marker, schema, predicate = selected
            self.assertTrue(predicate(ROOT))
            manifest = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest.get("schema"), schema)
            if schema == "hepta.agent-os-source-bundle.v1":
                self.assertEqual(
                    manifest.get("bundle_class"),
                    "agent-os-source-only")
            self.assertIs(manifest.get("paper_authorized"), False)
            self.assertIs(manifest.get("live_authorized"), False)
            records = manifest.get("files")
            self.assertIsInstance(records, list)
            listed = {
                record.get("path")
                for record in records
                if isinstance(record, dict)
            }
            for entrypoint in entrypoints:
                with self.subTest(entrypoint=entrypoint):
                    relative = f"scripts/{entrypoint}"
                    path = ROOT / relative
                    self.assertNotIn(relative, listed)
                    self.assertFalse(path.exists())
                    self.assertFalse(path.is_symlink())
            return
        for entrypoint in entrypoints:
            with self.subTest(entrypoint=entrypoint):
                path = ROOT / "scripts" / entrypoint
                source = path.read_text(encoding="utf-8")
                self.assertNotRegex(
                    source,
                    MODULE.FORBIDDEN_COMPATIBILITY_IMPORT_PATTERN,
                )
                completed = subprocess.run(
                    [sys.executable, str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(completed.returncode, 78)
                self.assertIn("quarantined", completed.stderr)

    def test_agent_os_source_excludes_legacy_entrypoints(self):
        root = self.fixture()
        for relative in MODULE.CANONICAL_RUNNERS:
            (root / relative).unlink()
        for retired in MODULE.RETIRED_ENTRYPOINTS:
            (root / "scripts" / retired).unlink()
        for relative in MODULE.FROZEN_LEGACY_ADAPTER_MUTATION_CALLS:
            (root / relative).unlink()
        for relative in MODULE.FROZEN_LEGACY_ADAPTER_MUTATION_SYMBOLS:
            (root / relative).unlink()
        self.write_profile_marker(
            root, agent_source=True,
            included_paths={"scripts/check_no_direct_broker_paths.py"})
        self.assertEqual(MODULE.violations(root), [])

    def test_agent_os_source_rejects_frozen_legacy_mutation_file(self):
        root = self.fixture()
        for relative in MODULE.CANONICAL_RUNNERS:
            (root / relative).unlink()
        for retired in MODULE.RETIRED_ENTRYPOINTS:
            (root / "scripts" / retired).unlink()
        self.write_profile_marker(
            root, agent_source=True,
            included_paths={
                "scripts/check_no_direct_broker_paths.py",
                "HeptaTrade/HeptaDemoStrategyTrader.cpp",
            })
        self.assertTrue(any(
            "frozen legacy broker mutation is present in an Agent OS "
            "source-only bundle" in item
            for item in MODULE.violations(root)
        ))

    def test_unsafe_agent_os_marker_cannot_remove_compatibility_gate(self):
        root = self.fixture()
        for relative in MODULE.CANONICAL_RUNNERS:
            (root / relative).unlink()
        marker = self.write_profile_marker(
            root, agent_source=True,
            included_paths={"scripts/check_no_direct_broker_paths.py"})
        document = json.loads(marker.read_text(encoding="utf-8"))
        document["paper_authorized"] = True
        marker.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        marker.chmod(0o644)
        self.assertTrue(any(
            "missing canonical offline regression runner" in item
            for item in MODULE.violations(root)))


if __name__ == "__main__":
    unittest.main()
