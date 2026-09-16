#!/usr/bin/env python3
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
path = root / "scripts/hepta_oms_lifecycle.py"
value = path.read_text()
needle = "import json\n"
if value.count(needle) != 1:
    raise SystemExit("unexpected lifecycle json import")
value = value.replace(needle, "import json\nimport math\n", 1)

# The compact simulator projection belongs only to ledgers that actually carry
# simulator economic history. Do not make an IB-only generation pay simulator
# recovery bytes or change its capacity semantics.
old = '''def _empty_simulator_state() -> dict[str, Any]:\n    return {"max_order_id": 999999, "admitted_orders": 0, "positions": {}}\n'''
new = '''def _empty_simulator_state() -> dict[str, Any]:\n    return {"present": False, "max_order_id": 999999, "admitted_orders": 0, "positions": {}}\n'''
if value.count(old) != 1:
    raise SystemExit("unexpected empty simulator state helper")
value = value.replace(old, new, 1)
old = '''            state = {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n'''
new = '''            state = {"present": True, "max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n'''
if value.count(old) != 1:
    raise SystemExit("unexpected checkpoint state construction")
value = value.replace(old, new, 1)
old = '''    maximum = int(state["max_order_id"])\n    base_maximum = maximum\n    admitted = int(state["admitted_orders"])\n    positions = dict(state["positions"])\n    places: dict[int, dict[str, Any]] = {}\n    fills: dict[int, dict[str, Any]] = {}\n    for event in events:\n        order_id = event.get("order_id", -1)\n        if type(order_id) is int and order_id > maximum:\n            maximum = order_id\n        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":\n            continue\n        is_fill = event.get("event") == "status" and event.get("status") == "Filled"\n        if event.get("event") != "place_sent" and not is_fill:\n            continue\n'''
new = '''    present = bool(state.get("present", False))\n    maximum = int(state["max_order_id"])\n    base_maximum = maximum\n    admitted = int(state["admitted_orders"])\n    positions = dict(state["positions"])\n    places: dict[int, dict[str, Any]] = {}\n    fills: dict[int, dict[str, Any]] = {}\n    for event in events:\n        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":\n            continue\n        is_fill = event.get("event") == "status" and event.get("status") == "Filled"\n        if event.get("event") != "place_sent" and not is_fill:\n            continue\n        present = True\n        order_id = event.get("order_id", -1)\n        if type(order_id) is int and order_id > maximum:\n            maximum = order_id\n'''
if value.count(old) != 1:
    raise SystemExit("unexpected simulator event loop")
value = value.replace(old, new, 1)
old = '''    return {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n\n\ndef _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:\n'''
new = '''    return {"present": present, "max_order_id": maximum, "admitted_orders": admitted, "positions": positions}\n\n\ndef _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:\n    if not state.get("present", False):\n        return []\n'''
if value.count(old) != 1:
    raise SystemExit("unexpected simulator projection boundary")
value = value.replace(old, new, 1)
path.write_text(value)

# execution_generation_support.cpp from PR #95 uses the compact HPM2 summary
# API. Bring the matching serializer/decoder pair as one reviewed unit rather
# than re-introducing the old macro-based implementation just to compile.
pr95 = "origin/remediation/full-priority-cleanup-20260916"
for relative in (
    "HeptaTrade/execution/paper_terminal_mutation_manifest.h",
    "HeptaTrade/execution/paper_terminal_mutation_manifest.cpp",
):
    content = subprocess.check_output(["git", "show", f"{pr95}:{relative}"], cwd=root, text=True)
    (root / relative).write_text(content)
