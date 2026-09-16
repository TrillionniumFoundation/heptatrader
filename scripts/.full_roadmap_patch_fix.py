#!/usr/bin/env python3
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PR95 = "origin/remediation/full-priority-cleanup-20260916"
MAIN = "origin/main"


def git_text(ref: str, path: str) -> str:
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)


def replace_once(value: str, old: str, new: str, label: str) -> str:
    count = value.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one replacement, got {count}")
    return value.replace(old, new, 1)


lifecycle_path = ROOT / "scripts/hepta_oms_lifecycle.py"
lifecycle = lifecycle_path.read_text()
lifecycle = replace_once(lifecycle, "import json\n", "import json\nimport math\n", "lifecycle import")
lifecycle = replace_once(
    lifecycle,
    'def _empty_simulator_state() -> dict[str, Any]:\n    return {"max_order_id": 999999, "admitted_orders": 0, "positions": {}}\n',
    'def _empty_simulator_state() -> dict[str, Any]:\n    return {"present": False, "max_order_id": 999999, "admitted_orders": 0, "positions": {}}\n',
    "empty simulator state")
lifecycle = replace_once(
    lifecycle,
    '            state = {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n',
    '            state = {"present": True, "max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n',
    "parsed simulator checkpoint")
lifecycle = replace_once(
    lifecycle,
    '''    maximum = int(state["max_order_id"])\n    base_maximum = maximum\n    admitted = int(state["admitted_orders"])\n    positions = dict(state["positions"])\n    places: dict[int, dict[str, Any]] = {}\n    fills: dict[int, dict[str, Any]] = {}\n    for event in events:\n        order_id = event.get("order_id", -1)\n        if type(order_id) is int and order_id > maximum:\n            maximum = order_id\n        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":\n            continue\n        is_fill = event.get("event") == "status" and event.get("status") == "Filled"\n        if event.get("event") != "place_sent" and not is_fill:\n            continue\n''',
    '''    present = bool(state.get("present", False))\n    maximum = int(state["max_order_id"])\n    base_maximum = maximum\n    admitted = int(state["admitted_orders"])\n    positions = dict(state["positions"])\n    places: dict[int, dict[str, Any]] = {}\n    fills: dict[int, dict[str, Any]] = {}\n    for event in events:\n        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":\n            continue\n        is_fill = event.get("event") == "status" and event.get("status") == "Filled"\n        if event.get("event") != "place_sent" and not is_fill:\n            continue\n        present = True\n        order_id = event.get("order_id", -1)\n        if type(order_id) is int and order_id > maximum:\n            maximum = order_id\n''',
    "simulator event projection")
lifecycle = replace_once(
    lifecycle,
    '    return {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n\n\ndef _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:\n',
    '    return {"present": present, "max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n\n\ndef _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:\n    if not state.get("present", False):\n        return []\n',
    "simulator state projection")
lifecycle_path.write_text(lifecycle)

# Keep current main's owner-session HPM1 terminal-fence semantics. Only absorb
# PR95 changes that preserve that authority boundary: the ordinary .cpp build
# unit and sorted send-attempt index/window reader.
header_path = ROOT / "HeptaTrade/oms_generation_store.h"
header = git_text(MAIN, "HeptaTrade/oms_generation_store.h")
header = replace_once(
    header,
    "    bool m_segmentedTail = false;\n",
    "    bool m_segmentedTail = false;\n    bool m_sendIndexWindowSorted = false;\n",
    "generation header sorted-index flag")
header_path.write_text(header)

support_path = ROOT / "HeptaTrade/execution/execution_generation_support.cpp"
support = support_path.read_text()
support = replace_once(
    support,
    "const std::size_t kMaximumGenerationIndexLineBytes = 64U * 1024U;\n",
    "const std::size_t kMaximumGenerationIndexLineBytes = 64U * 1024U;\nconst std::size_t kMaximumTerminalMutationRecords = 4097U;\n",
    "terminal mutation bound")
main_inc = git_text(MAIN, "HeptaTrade/execution/execution_generation_support.inc")

# Replace only the mutation enumerator with main's owner-session implementation.
start = support.index("bool OmsGenerationStore::EnumerateMutationRecords(")
end = support.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore(", start)
main_start = main_inc.index("bool OmsGenerationStore::EnumerateMutationRecords(")
main_end = main_inc.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore(", main_start)
support = support[:start] + main_inc[main_start:main_end] + support[end:]

# Replace only the HPM2 terminal-fence projection function. The PR95 capacity
# bridge and V2 public dispatch that follow it are retained byte-for-byte.
enter = "bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectGenerationAwareLocked("
start = support.index(enter)
capacity_suffix = "\n// Capacity bridge compiled after execution_generation_support.inc so it can\n"
end = support.index(capacity_suffix, start)
main_start = main_inc.index(enter)
support = support[:start] + main_inc[main_start:] + support[end:]
support_path.write_text(support)

# Keep the reviewed machine-readable build inventory synchronized with the
# normal translation unit introduced above. This is source ownership metadata,
# not an extra acceptance gate.
build_targets_path = ROOT / "docs/build-targets.json"
build_targets = build_targets_path.read_text()
terminal_entry = '''            {\n              "path": "HeptaTrade/execution/execution_coordinator_terminal.cpp",\n              "kind": "implementation",\n              "owner": "execution-service",\n              "language": "CXX",\n              "standard": "11"\n            },\n            {\n              "path": "HeptaTrade/execution/execution_place_order_dispatch.cpp",'''
generation_entry = '''            {\n              "path": "HeptaTrade/execution/execution_coordinator_terminal.cpp",\n              "kind": "implementation",\n              "owner": "execution-service",\n              "language": "CXX",\n              "standard": "11"\n            },\n            {\n              "path": "HeptaTrade/execution/execution_generation_support.cpp",\n              "kind": "implementation",\n              "owner": "execution-service",\n              "language": "CXX",\n              "standard": "11"\n            },\n            {\n              "path": "HeptaTrade/execution/execution_place_order_dispatch.cpp",'''
build_targets = replace_once(
    build_targets, terminal_entry, generation_entry, "build inventory generation source")
build_targets_path.write_text(build_targets)

# The migration removes the textual V2 implementation file, so the closed gap
# must cite the normal translation unit that now carries the same native V2
# recovery/capacity dispatch.
gap_path = ROOT / "docs/gap-register.json"
gap = gap_path.read_text()
gap = replace_once(
    gap,
    '"HeptaTrade/execution/execution_generation_v2_support.inc"',
    '"HeptaTrade/execution/execution_generation_support.cpp"',
    "OMS gap evidence path")
gap_path.write_text(gap)
