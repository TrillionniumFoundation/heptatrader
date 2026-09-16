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
# Keep PR95's sorted-index helpers and all public V2 wrappers intact.
start = support.index("bool OmsGenerationStore::EnumerateMutationRecords(")
end = support.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore(", start)
main_start = main_inc.index("bool OmsGenerationStore::EnumerateMutationRecords(")
main_end = main_inc.index("ExecutionCoordinator::RequestRecordStore::RequestRecordStore(", main_start)
support = support[:start] + main_inc[main_start:main_end] + support[end:]

# Replace only the HPM2 terminal-fence projection function. The V2 generation
# dispatch/capacity section follows it in PR95 and must remain in the normal
# translation unit; an earlier patch accidentally truncated that suffix.
enter = "bool ExecutionCoordinator::EnterPaperTerminalFenceAndProjectGenerationAwareLocked("
start = support.index(enter)
v2_suffix = "\nnamespace\n{\nconst char* const kRuntimeManifestHeaderV2 = \"HEPTA_OMS_RUNTIME_GENERATION_V2\";"
end = support.index(v2_suffix, start)
main_start = main_inc.index(enter)
support = support[:start] + main_inc[main_start:] + support[end:]
support_path.write_text(support)
