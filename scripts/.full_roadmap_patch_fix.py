#!/usr/bin/env python3
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = root / path
    value = target.read_text()
    if value.count(old) != 1:
        raise SystemExit(f"{path}: expected one replacement, got {value.count(old)}")
    target.write_text(value.replace(old, new, 1))


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

# PR95's first HPM2 draft summarized every mutation on an account/domain. The
# established terminal-fence authority is narrower: one owner agent/session.
# Preserve that authority boundary while retaining the fixed-size sealed-history
# digest. This is a semantic fix, not a test relaxation.
replace_once(
    "HeptaTrade/oms_generation_store.h",
    '''    bool SummarizeMutationRecords(\n        const std::string& account,\n        const std::string& executionDomain,\n        OmsGenerationMutationSummary& summary,\n        std::string& reason) const;\n''',
    '''    bool SummarizeMutationRecords(\n        const std::string& agentId,\n        const std::string& sessionId,\n        const std::string& account,\n        const std::string& executionDomain,\n        OmsGenerationMutationSummary& summary,\n        std::string& reason) const;\n''')
replace_once(
    "HeptaTrade/execution/execution_generation_support.cpp",
    '''bool OmsGenerationStore::SummarizeMutationRecords(\n    const std::string& account,\n    const std::string& executionDomain,\n    OmsGenerationMutationSummary& summary,\n    std::string& reason) const\n''',
    '''bool OmsGenerationStore::SummarizeMutationRecords(\n    const std::string& agentId,\n    const std::string& sessionId,\n    const std::string& account,\n    const std::string& executionDomain,\n    OmsGenerationMutationSummary& summary,\n    std::string& reason) const\n''')
replace_once(
    "HeptaTrade/execution/execution_generation_support.cpp",
    '''        if (fields[12] == "1" && rowAccount == account &&\n            rowDomain == executionDomain)\n''',
    '''        if (fields[12] == "1" && fields[0] == GenerationHex(agentId) &&\n            fields[1] == GenerationHex(sessionId) && rowAccount == account &&\n            rowDomain == executionDomain)\n''')
replace_once(
    "HeptaTrade/execution/execution_generation_support.cpp",
    '''    if (!m_generationStore.SummarizeMutationRecords(\n            binding.owner.account, binding.owner.executionDomain,\n            sealed, reason))\n''',
    '''    if (!m_generationStore.SummarizeMutationRecords(\n            binding.owner.agentId, binding.owner.sessionId,\n            binding.owner.account, binding.owner.executionDomain, sealed, reason))\n''')
replace_once(
    "HeptaTrade/execution/execution_generation_support.cpp",
    '''        if (!request.durableMutationIntent ||\n            request.context.account != binding.owner.account ||\n            request.context.executionDomain != binding.owner.executionDomain)\n''',
    '''        if (!request.durableMutationIntent ||\n            request.context.agentId != binding.owner.agentId ||\n            request.context.sessionId != binding.owner.sessionId ||\n            request.context.account != binding.owner.account ||\n            request.context.executionDomain != binding.owner.executionDomain)\n''')

# HPM2 deliberately replaces the materialized HPM1 command vector with a
# fixed-size digest/count. Update the regression to assert the new representation
# while still proving the foreign session is excluded and remains queryable.
replace_once(
    "tests/oms_recovery_growth_probe.h",
    '''        assert(recovered.EnterPaperTerminalFenceAndProject(binding, universe, reason));\n        assert(universe.commands.size() == 2);\n        for (std::size_t i = 0; i < universe.commands.size(); ++i)\n        {\n            assert(universe.commands[i].agentId == oldCommand.context.agentId);\n            assert(universe.commands[i].sessionId == oldCommand.context.sessionId);\n            assert(universe.commands[i].toolCallId != foreignCommand.context.toolCallId);\n        }\n''',
    '''        if (!recovered.EnterPaperTerminalFenceAndProject(binding, universe, reason))\n        {\n            std::fprintf(stderr, "terminal HPM2 projection failed: %s\\n", reason.c_str());\n            assert(false);\n        }\n        assert(universe.compactSummary);\n        assert(universe.commands.empty());\n        assert(universe.correlations.empty());\n        assert(universe.commandCount == 2);\n        assert(!universe.commandSetSha256.empty());\n        assert(!universe.correlationSetSha256.empty());\n''')
